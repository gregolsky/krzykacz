import logging

from krzykacz.audit import log_call


def test_log_call_includes_ip_action_and_fields(caplog):
    with caplog.at_level(logging.INFO, logger="krzykacz.audit"):
        log_call("publish", "127.0.0.1", voice="justyna", status="queued")

    assert "ip=127.0.0.1" in caplog.text
    assert "action=publish" in caplog.text
    assert "voice='justyna'" in caplog.text
    assert "status='queued'" in caplog.text


def test_log_call_escapes_embedded_newline_in_a_field_value(caplog):
    with caplog.at_level(logging.INFO, logger="krzykacz.audit"):
        log_call("mcp.send_message", "9.9.9.9", voice="x\nip=6.6.6.6 action=publish")

    # A literal newline in caller-controlled input (e.g. MCP's `voice`) must
    # not split into a second, forged-looking log line.
    records = [r for r in caplog.records if r.name == "krzykacz.audit"]
    assert len(records) == 1
    assert "\\n" in records[0].getMessage()


def test_log_call_escapes_embedded_key_value_pair_in_a_field_value(caplog):
    with caplog.at_level(logging.INFO, logger="krzykacz.audit"):
        log_call("mcp.send_message", "9.9.9.9", voice="ok status=202 ip=6.6.6.6")

    # The forged "status=202 ip=6.6.6.6" must stay inside the quoted `voice`
    # value as a single token, not read back as separate top-level fields.
    assert "voice='ok status=202 ip=6.6.6.6'" in caplog.text


def test_log_call_skips_formatting_fields_when_the_logger_is_disabled():
    # Not just "no visible line" -- a field's repr() must genuinely never
    # run, since building it (not the logging call itself) is the cost the
    # isEnabledFor guard exists to avoid on every audited request.
    class ExplodingOnRepr:
        def __repr__(self):
            raise AssertionError("log_call formatted a field despite being disabled")

    audit_logger = logging.getLogger("krzykacz.audit")
    original_level = audit_logger.level
    audit_logger.setLevel(logging.WARNING)
    try:
        log_call("publish", "127.0.0.1", payload=ExplodingOnRepr())
    finally:
        audit_logger.setLevel(original_level)
