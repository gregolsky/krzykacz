import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LISTENER = REPO / "scripts" / "deploy-listener.sh"


class RawStream(BaseHTTPRequestHandler):
    """ntfy's /raw stream: one message body per line, then the stream ends."""

    lines = b"hello\ndeploy-krzykacz now\n\ndeploy-krzykacz\ndeploy-krzykacz\n"

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.lines)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("min_interval, expected_deploys", [("0", 2), ("300", 1)])
def test_listener_deploys_only_on_the_exact_signal(tmp_path, min_interval, expected_deploys):
    server = ThreadingHTTPServer(("127.0.0.1", 0), RawStream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = tmp_path / "calls.log"
    stub = tmp_path / "bin" / "ansible-pull"
    stub.parent.mkdir()
    stub.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n')
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "KRZYKACZ_NTFY_SERVER": f"http://127.0.0.1:{server.server_address[1]}",
        "KRZYKACZ_DEPLOY_TOPIC": "t",
        "KRZYKACZ_DEPLOY_MIN_INTERVAL": min_interval,
    }
    proc = subprocess.Popen(
        [str(LISTENER)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not calls.exists():
            time.sleep(0.05)
        time.sleep(0.3)  # would catch a second, wrongly triggered deploy
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
        server.shutdown()

    # Two exact signals arrive; the second is skipped while within the interval.
    assert calls.read_text().splitlines() == [
        "-U https://github.com/gregolsky/krzykacz.git -C main"
        " -d /var/lib/krzykacz-deploy/checkout local.yml"
    ] * expected_deploys


def test_listener_refuses_to_start_without_a_topic():
    env = {k: v for k, v in os.environ.items() if k != "KRZYKACZ_DEPLOY_TOPIC"}
    result = subprocess.run([str(LISTENER)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "KRZYKACZ_DEPLOY_TOPIC" in result.stderr


def test_playbook_references_files_that_exist():
    yaml = pytest.importorskip("yaml")
    tasks = yaml.safe_load((REPO / "local.yml").read_text())[0]["tasks"]
    text = str(tasks)
    for path in ("requirements.txt", "scripts/install-service.sh", "systemd/krzykacz-deploy.service"):
        assert path in text or Path(path).name in text, path
        assert (REPO / path).exists(), path
