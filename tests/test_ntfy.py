import json

from krzykacz.ntfy import _handle_line
from krzykacz.protocol import Command, Msg, Repeat


def line(message="", tags=None, event="message"):
    payload = {"event": event, "message": message}
    if tags is not None:
        payload["tags"] = tags
    return json.dumps(payload).encode("utf-8")


class Recorder:
    def __init__(self):
        self.messages = []
        self.commands = []

    def submit(self, envelope):
        self.messages.append(envelope)
        return True

    def command(self, command):
        self.commands.append(command)


def test_plain_message_is_submitted():
    r = Recorder()
    _handle_line(line("czesc"), r.submit, r.command)
    assert r.messages == [Msg(content="czesc")]
    assert r.commands == []


def test_mute_request_goes_to_on_command_and_is_never_spoken():
    r = Recorder()
    # ntfy fills an empty body with "triggered" -- it must not be read aloud.
    _handle_line(line("triggered", ["mute=1"]), r.submit, r.command)
    assert r.commands == [Command(mute=True)]
    assert r.messages == []


def test_unmute_and_status_requests():
    r = Recorder()
    _handle_line(line("triggered", ["mute=0"]), r.submit, r.command)
    _handle_line(line("triggered", ["status=1"]), r.submit, r.command)
    assert r.commands == [Command(mute=False), Command(status=True)]
    assert r.messages == []


def test_control_request_without_a_handler_is_dropped_not_spoken():
    r = Recorder()
    _handle_line(line("triggered", ["mute=1"]), r.submit)
    assert r.messages == []


def test_a_failing_command_handler_does_not_escape():
    r = Recorder()

    def boom(command):
        raise RuntimeError("boom")

    _handle_line(line("triggered", ["mute=1"]), r.submit, boom)
    assert r.messages == []


def test_replay_still_works_alongside_command_handling():
    r = Recorder()
    _handle_line(line("", ["replay=-1"]), r.submit, r.command)
    assert r.messages == [Repeat(number=-1)]
    assert r.commands == []


def test_non_message_events_are_ignored():
    r = Recorder()
    _handle_line(line(event="keepalive"), r.submit, r.command)
    assert r.messages == []
    assert r.commands == []
