from krzykacz.metadata import describe, list_effects


def test_list_effects_returns_sorted_filenames(tmp_path):
    (tmp_path / "zeta.ogg").write_bytes(b"x")
    (tmp_path / "alpha.mp3").write_bytes(b"x")

    assert list_effects(str(tmp_path)) == ["alpha.mp3", "zeta.ogg"]


def test_list_effects_ignores_directories(tmp_path):
    (tmp_path / "boom.ogg").write_bytes(b"x")
    (tmp_path / "subdir").mkdir()

    assert list_effects(str(tmp_path)) == ["boom.ogg"]


def test_list_effects_missing_directory_is_empty(tmp_path):
    assert list_effects(str(tmp_path / "nope")) == []


def test_describe_reports_backend_voices_and_effects(tmp_path):
    (tmp_path / "boom.ogg").write_bytes(b"x")

    result = describe("piper", ["darkman", "justyna"], "darkman", str(tmp_path))

    assert result == {
        "tts": "piper",
        "voices": ["darkman", "justyna"],
        "default_voice": "darkman",
        "effects": ["boom.ogg"],
    }


def test_describe_copies_voices_rather_than_aliasing(tmp_path):
    voices = ["darkman"]

    result = describe("piper", voices, "darkman", str(tmp_path))
    voices.append("mutated")

    assert result["voices"] == ["darkman"]
