from krzykacz.metadata import describe_effects, describe_limits, describe_voices, list_effects
from krzykacz.protocol import (
    MAX_CONTENT_BYTES,
    MAX_REPEAT_COUNT,
    MAX_SPOKEN_BYTES,
    RHYTHM_RANGE,
    SPEED_RANGE,
    VARIATION_RANGE,
)
from krzykacz.random_picks import INTENSITIES, STYLES


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


def test_describe_voices_reports_backend_and_names():
    assert describe_voices("piper", ["darkman", "justyna"], "darkman") == {
        "tts": "piper",
        "voices": ["darkman", "justyna"],
        "default_voice": "darkman",
    }


def test_describe_voices_copies_names_rather_than_aliasing():
    names = ["darkman"]

    result = describe_voices("piper", names, "darkman")
    names.append("mutated")

    assert result["voices"] == ["darkman"]


def test_describe_effects_reports_files_in_assets_dir(tmp_path):
    (tmp_path / "boom.ogg").write_bytes(b"x")

    assert describe_effects(str(tmp_path)) == {"effects": ["boom.ogg"]}


def test_describe_effects_rereads_the_directory_on_each_call(tmp_path):
    assert describe_effects(str(tmp_path)) == {"effects": []}

    (tmp_path / "late.ogg").write_bytes(b"x")

    assert describe_effects(str(tmp_path)) == {"effects": ["late.ogg"]}


def test_describe_limits_reports_protocol_and_instance_caps():
    assert describe_limits(10, 5, 2.5, INTENSITIES, STYLES) == {
        "max_content_bytes": MAX_CONTENT_BYTES,
        "max_spoken_bytes": MAX_SPOKEN_BYTES,
        "max_repeat": MAX_REPEAT_COUNT,
        "history_size": 10,
        "queue_size": 5,
        "rate_limit_interval": 2.5,
        "speed_range": list(SPEED_RANGE),
        "variation_range": list(VARIATION_RANGE),
        "rhythm_range": list(RHYTHM_RANGE),
        "curse_intensities": list(INTENSITIES),
        "curse_styles": list(STYLES),
    }


def test_describe_limits_ranges_are_json_friendly_lists():
    # They travel over HTTP as JSON; a tuple would serialize the same, but
    # asserting the type keeps the payload contract explicit.
    limits = describe_limits(10, 5, 2.5, INTENSITIES, STYLES)

    assert isinstance(limits["speed_range"], list)
    assert limits["speed_range"] == [0.5, 2.0]
    assert isinstance(limits["curse_intensities"], list)
    assert isinstance(limits["curse_styles"], list)


def test_the_three_views_do_not_overlap_in_keys():
    # Nothing merges them any more, but a caller fetching all three still
    # shouldn't have to resolve a key collision between them.
    voices = describe_voices("piper", ["darkman"], "darkman")
    effects = describe_effects("/nonexistent")
    limits = describe_limits(10, 5, 2.5, INTENSITIES, STYLES)

    assert set(voices) & set(effects) == set()
    assert set(voices) & set(limits) == set()
    assert set(effects) & set(limits) == set()
