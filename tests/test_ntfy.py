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
