"""Install the ``modelscompare`` command into the user's PATH."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


class InstallError(RuntimeError):
    """Raised when the command cannot be installed."""


@dataclass(frozen=True, slots=True)
class InstallResult:
    installed: bool
    command_path: Path | None
    binary_path: Path | None
    path_updated: bool
    message: str
    needs_new_terminal: bool = False


def _is_windows(platform_name: str) -> bool:
    return platform_name.casefold() in {"nt", "windows"}


def default_install_dir(platform_name: str | None = None) -> Path:
    platform_name = platform_name or os.name
    override = os.environ.get("MODELSCOMPARE_INSTALL_DIR")
    if override:
        return Path(override).expanduser()
    if _is_windows(platform_name):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Programs" / "modelscompare"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "modelscompare"


def default_bin_dir(platform_name: str | None = None) -> Path:
    platform_name = platform_name or os.name
    override = os.environ.get("MODELSCOMPARE_BIN_DIR")
    if override:
        return Path(override).expanduser()
    if _is_windows(platform_name):
        return default_install_dir(platform_name)
    return Path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local" / "bin"))


def _directory_on_path(directory: Path) -> bool:
    target = os.path.normcase(os.path.abspath(str(directory)))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = os.path.expandvars(os.path.expanduser(entry))
        if os.path.normcase(os.path.abspath(candidate)) == target:
            return True
    return False


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".new")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise InstallError(f"Impossibile installare {target}: {error}") from error


def _install_binary(source: Path, destination: Path, platform_name: str) -> None:
    if source.resolve() == destination.resolve():
        return
    _atomic_copy(source, destination)
    if not _is_windows(platform_name):
        destination.chmod(0o755)


def _link_command(destination: Path, command_path: Path, platform_name: str) -> Path:
    if _is_windows(platform_name):
        return destination
    command_path.parent.mkdir(parents=True, exist_ok=True)
    if command_path.resolve() == destination.resolve():
        return command_path
    try:
        if command_path.is_symlink() or command_path.exists():
            command_path.unlink()
        command_path.symlink_to(destination)
    except OSError:
        _atomic_copy(destination, command_path)
        command_path.chmod(0o755)
    return command_path


def _install_launcher(bin_dir: Path, platform_name: str) -> Path:
    """Create a launcher for a source installation (``python -m modelscompare``)."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    if _is_windows(platform_name):
        command_path = bin_dir / "modelscompare.cmd"
        command_path.write_text(
            f'@echo off\r\n"{sys.executable}" -m modelscompare %*\r\n',
            encoding="utf-8",
        )
        return command_path

    command_path = bin_dir / "modelscompare"
    command_path.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m modelscompare \"$@\"\n",
        encoding="utf-8",
    )
    command_path.chmod(0o755)
    return command_path


def _default_profile_path(shell: str) -> Path:
    home = Path.home()
    shell = shell.casefold()
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "bash":
        return home / ".bashrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    return home / ".profile"


def ensure_unix_path(
    directory: Path,
    *,
    profile_path: Path | None = None,
    shell: str | None = None,
) -> bool:
    """Append an idempotent PATH block to the user's shell profile."""

    if _directory_on_path(directory):
        return False

    shell_name = (shell or Path(os.environ.get("SHELL", "")).name or "sh").casefold()
    profile = profile_path or _default_profile_path(shell_name)
    marker = "# >>> modelscompare >>>"
    try:
        existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
    except OSError as error:
        raise InstallError(f"Impossibile leggere {profile}: {error}") from error

    if marker in existing:
        return False

    if shell_name == "fish":
        block = (
            "# >>> modelscompare >>>\n"
            f"fish_add_path {shlex.quote(str(directory))}\n"
            "# <<< modelscompare <<<"
        )
    else:
        quoted = shlex.quote(str(directory))
        block = (
            "# >>> modelscompare >>>\n"
            "case \":$PATH:\" in\n"
            f"  *\":{directory}:\"*) ;;\n"
            f"  *) export PATH={quoted}:\"$PATH\" ;;\n"
            "esac\n"
            "# <<< modelscompare <<<"
        )

    try:
        profile.parent.mkdir(parents=True, exist_ok=True)
        with profile.open("a", encoding="utf-8") as file:
            if existing and not existing.endswith("\n"):
                file.write("\n")
            file.write("\n" + block + "\n")
    except OSError as error:
        raise InstallError(f"Impossibile aggiornare {profile}: {error}") from error
    return True


def _broadcast_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            0x001A,  # WM_SETTINGCHANGE
            0,
            "Environment",
            0x0002,  # SMTO_ABORTIFHUNG
            5000,
            None,
        )
    except Exception:
        pass


def _add_windows_path(directory: Path) -> bool:
    try:
        import winreg
    except ImportError as error:
        raise InstallError("Registro di Windows non disponibile.") from error

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                current, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, value_type = "", winreg.REG_EXPAND_SZ
            entries = [entry for entry in str(current).split(";") if entry]
            target = os.path.normcase(os.path.expandvars(str(directory)))
            if any(
                os.path.normcase(os.path.expandvars(entry)) == target
                for entry in entries
            ):
                return False
            entries.append(str(directory))
            winreg.SetValueEx(
                key,
                "Path",
                0,
                value_type or winreg.REG_EXPAND_SZ,
                ";".join(entries),
            )
    except OSError as error:
        raise InstallError(f"Impossibile aggiornare il PATH di Windows: {error}") from error

    _broadcast_environment_change()
    return True


def install_command(
    *,
    source: Path | None = None,
    update_path: bool = True,
    platform_name: str | None = None,
) -> InstallResult:
    """Install the command and add its directory to the user PATH.

    ``source`` is mainly useful for tests. In normal use the running
    standalone executable or the current Python interpreter is used.
    """

    platform_name = platform_name or os.name
    frozen = source is not None or bool(getattr(sys, "frozen", False))
    install_dir = default_install_dir(platform_name)
    bin_dir = default_bin_dir(platform_name)

    if not frozen:
        existing = shutil.which("modelscompare")
        if existing:
            return InstallResult(
                True,
                Path(existing),
                None,
                False,
                f"Il comando modelscompare è già disponibile in {existing}.",
            )

    try:
        if frozen:
            source_path = Path(source or sys.executable).resolve()
            if not source_path.is_file():
                raise InstallError(f"Eseguibile non trovato: {source_path}")
            binary_name = "modelscompare.exe" if _is_windows(platform_name) else "modelscompare"
            binary_path = install_dir / binary_name
            _install_binary(source_path, binary_path, platform_name)
            command_path = _link_command(
                binary_path,
                bin_dir / binary_name,
                platform_name,
            )
        else:
            binary_path = None
            command_path = _install_launcher(bin_dir, platform_name)
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(f"Installazione non riuscita: {error}") from error

    path_updated = False
    if update_path and not _directory_on_path(bin_dir):
        path_updated = (
            _add_windows_path(bin_dir)
            if _is_windows(platform_name)
            else ensure_unix_path(bin_dir)
        )

    on_path = _directory_on_path(bin_dir)
    message = f"Comando installato in {command_path}."
    if path_updated:
        message += " PATH aggiornato: apri un nuovo terminale."
    elif not on_path:
        message += f" Aggiungi {bin_dir} al PATH per usarlo da qualsiasi cartella."
    else:
        message += " Il comando è già disponibile nel PATH."
    return InstallResult(
        installed=True,
        command_path=command_path,
        binary_path=binary_path,
        path_updated=path_updated,
        message=message,
        needs_new_terminal=path_updated or not on_path,
    )
