import os
import shutil
from pathlib import Path

from modelscompare.installer import ensure_unix_path, install_command


def _fake_binary(tmp_path: Path, name: str = "modelscompare-binary") -> Path:
    source = tmp_path / name
    source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    source.chmod(0o755)
    return source


def test_install_binary_creates_command_in_user_bin(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "data" / "modelscompare"
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("MODELSCOMPARE_INSTALL_DIR", str(install_dir))
    monkeypatch.setenv("MODELSCOMPARE_BIN_DIR", str(bin_dir))
    source = _fake_binary(tmp_path)

    result = install_command(source=source, update_path=False, platform_name="posix")

    assert result.installed
    assert result.binary_path == install_dir / "modelscompare"
    assert result.binary_path.exists()
    assert result.command_path is not None
    assert result.command_path.exists()
    assert os.access(result.command_path, os.X_OK)


def test_install_binary_uses_windows_executable_name(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "app"
    monkeypatch.setenv("MODELSCOMPARE_INSTALL_DIR", str(install_dir))
    monkeypatch.setenv("MODELSCOMPARE_BIN_DIR", str(install_dir))
    source = _fake_binary(tmp_path, "ModelsCompare.exe")

    result = install_command(source=source, update_path=False, platform_name="nt")

    assert result.installed
    assert result.binary_path == install_dir / "modelscompare.exe"
    assert result.binary_path.exists()
    assert result.command_path == result.binary_path


def test_ensure_unix_path_is_idempotent(tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    profile = tmp_path / ".bashrc"
    profile.write_text("# existing\n", encoding="utf-8")

    assert ensure_unix_path(bin_dir, profile_path=profile, shell="bash")
    updated = profile.read_text(encoding="utf-8")
    assert "# >>> modelscompare >>>" in updated
    assert str(bin_dir) in updated
    assert not ensure_unix_path(bin_dir, profile_path=profile, shell="bash")
    assert profile.read_text(encoding="utf-8").count("# >>> modelscompare >>>") == 1


def test_ensure_unix_path_uses_fish_syntax(tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    profile = tmp_path / "config.fish"

    assert ensure_unix_path(bin_dir, profile_path=profile, shell="fish")

    assert "fish_add_path" in profile.read_text(encoding="utf-8")


def test_source_install_creates_launcher(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("MODELSCOMPARE_BIN_DIR", str(bin_dir))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = install_command(update_path=False, platform_name="posix")

    assert result.installed
    assert result.command_path is not None
    assert result.command_path.exists()
    assert "-m modelscompare" in result.command_path.read_text(encoding="utf-8")
