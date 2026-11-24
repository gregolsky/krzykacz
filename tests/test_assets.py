from krzykacz.assets import list_effects, resolve_effect


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


def test_resolve_effect_finds_a_file_in_the_directory(tmp_path):
    (tmp_path / "boom.ogg").write_bytes(b"x")

    assert resolve_effect(tmp_path.resolve(), "boom.ogg") == (tmp_path / "boom.ogg").resolve()


def test_resolve_effect_missing_file_is_none(tmp_path):
    assert resolve_effect(tmp_path.resolve(), "nope.ogg") is None


def test_resolve_effect_rejects_traversal(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "secret").write_bytes(b"x")

    assert resolve_effect(assets.resolve(), "../secret") is None


def test_resolve_effect_rejects_symlink_out_of_the_directory(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "secret").write_bytes(b"x")
    (assets / "link.ogg").symlink_to(tmp_path / "secret")

    assert resolve_effect(assets.resolve(), "link.ogg") is None
