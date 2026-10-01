import json

import requests

from krzykacz.status import StatusPublisher


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))


class FakePost:
    def __init__(self):
        self.calls = []
        self.fail_with = None
        self.status = 200

    def __call__(self, url, data, timeout):
        self.calls.append((url, json.loads(data.decode("utf-8"))))
        if self.fail_with is not None:
            raise self.fail_with
        return FakeResponse(self.status)


IDLE = {"playing": None, "pending": [], "muted": False}


def make(state, post, min_interval=0.0):
    return StatusPublisher(
        "https://ntfy.example/",
        "krzyk-status",
        lambda: dict(state),
        poll_interval=0,
        min_publish_interval=min_interval,
        post=post,
    )


def test_publishes_the_view_as_json_to_the_status_topic():
    post = FakePost()
    publisher = make(IDLE, post)

    assert publisher.poll_once() is True
    assert post.calls == [("https://ntfy.example/krzyk-status", IDLE)]


def test_polish_text_is_sent_as_utf8():
    post = FakePost()
    state = dict(IDLE, playing={"content": "Zażółć gęślą jaźń", "voice": None, "repeat": 1})
    publisher = make(state, post)

    publisher.poll_once()
    assert post.calls[0][1]["playing"]["content"] == "Zażółć gęślą jaźń"


def test_unchanged_view_is_not_published_again():
    post = FakePost()
    publisher = make(IDLE, post)

    publisher.poll_once()
    assert publisher.poll_once() is False
    assert len(post.calls) == 1


def test_a_change_is_published():
    post = FakePost()
    state = dict(IDLE)
    publisher = make(state, post)

    publisher.poll_once()
    state["muted"] = True
    assert publisher.poll_once() is True
    assert post.calls[-1][1]["muted"] is True


def test_request_forces_a_publish_of_an_unchanged_view():
    post = FakePost()
    publisher = make(IDLE, post)

    publisher.poll_once()
    publisher.request()
    assert publisher.poll_once() is True
    assert len(post.calls) == 2


def test_publishes_are_throttled_and_the_change_goes_out_once_allowed():
    post = FakePost()
    state = dict(IDLE)
    publisher = make(state, post, min_interval=3600)

    publisher.poll_once()
    state["muted"] = True
    assert publisher.poll_once() is False
    assert len(post.calls) == 1

    publisher._min_publish_interval = 0
    assert publisher.poll_once() is True
    assert post.calls[-1][1]["muted"] is True


def test_a_failed_publish_is_retried_on_the_next_poll():
    post = FakePost()
    publisher = make(IDLE, post)

    post.fail_with = requests.ConnectionError("offline")
    assert publisher.poll_once() is False
    post.fail_with = None
    assert publisher.poll_once() is True
    assert len(post.calls) == 2


def test_an_http_error_status_counts_as_a_failed_publish():
    post = FakePost()
    publisher = make(IDLE, post)

    post.status = 429
    assert publisher.poll_once() is False
    post.status = 200
    assert publisher.poll_once() is True
