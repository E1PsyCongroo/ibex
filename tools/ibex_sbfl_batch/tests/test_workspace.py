from pathlib import Path

from ibex_sbfl_batch.workspace import WORKDIR_DIRECTORIES, copy_workdir


def test_copy_workdir_uses_explicit_whitelist(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    for name in WORKDIR_DIRECTORIES:
        directory = source / name
        directory.mkdir()
        (directory / "marker").write_text(name, encoding="utf-8")
    (source / "Cargo.lock").write_text("lock", encoding="utf-8")
    (source / "Cargo.toml").write_text("manifest", encoding="utf-8")
    (source / "ibex.core").write_text("core", encoding="utf-8")

    copy_workdir(source, destination)

    for name in WORKDIR_DIRECTORIES:
        assert destination.joinpath(name, "marker").is_file()
    assert not destination.joinpath("doc").exists()
    assert destination.joinpath("ibex.core").is_file()
    assert destination.joinpath("Cargo.lock").is_file()
