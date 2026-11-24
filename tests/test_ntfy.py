import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from krzykacz.ntfy import listen
from krzykacz.protocol import Msg


class OneShortMessageThenSilence(BaseHTTPRequestHandler):
    """Like ntfy's JSON stream: one short message, then the connection stays
    open with nothing more to say (keepalives only come every ~45 s)."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write((json.dumps({"event": "message", "message": "hej"}) + "\n").encode())
        self.wfile.flush()
        time.sleep(10)

    def log_message(self, *args):
        pass


def test_a_short_message_is_delivered_without_waiting_for_more_data():
    # Regression: iter_lines()' default 512-byte chunks held a short message
    # until keepalives filled the buffer -- minutes on the real ntfy.sh.
    server = ThreadingHTTPServer(("127.0.0.1", 0), OneShortMessageThenSilence)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    received = []
    delivered = threading.Event()

    def on_message(envelope):
        received.append(envelope)
        delivered.set()
        return True

    url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=listen, args=(url, "topic", on_message), daemon=True).start()
    try:
        assert delivered.wait(timeout=3), "short message was held back"
        assert received == [Msg(content="hej")]
    finally:
        server.shutdown()


class FailsThenDropsBetweenMessages(BaseHTTPRequestHandler):
    """1st connection: HTTP 500. 2nd: one message, then the server hangs up.
    3rd: another message."""

    requests_seen = 0

    def do_GET(self):
        cls = type(self)
        cls.requests_seen += 1
        if cls.requests_seen == 1:
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        text = "raz" if cls.requests_seen == 2 else "dwa"
        self.wfile.write((json.dumps({"event": "message", "message": text}) + "\n").encode())
        self.wfile.flush()
        if cls.requests_seen > 2:
            time.sleep(10)

    def log_message(self, *args):
        pass


def test_listener_reconnects_after_an_error_and_a_dropped_connection(monkeypatch):
    import types
    import krzykacz.ntfy as ntfy_mod

    both = threading.Event()
    # Skip the backoff while reconnecting; once done, park the listener thread for good.
    monkeypatch.setattr(ntfy_mod, "time", types.SimpleNamespace(
        sleep=lambda s: both.is_set() and threading.Event().wait()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), FailsThenDropsBetweenMessages)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    received = []

    def on_message(envelope):
        received.append(envelope.content)
        if len(received) == 2:
            both.set()
        return True

    url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=listen, args=(url, "topic", on_message), daemon=True).start()
    try:
        assert both.wait(timeout=5), f"got only {received}"
        assert received == ["raz", "dwa"]
    finally:
        server.shutdown()
