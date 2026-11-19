"""Filesystem locations shared by exports, caches, and platform builds."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_HOME_TOKEN = re.compile(r"\$(?:\{HOME\}|HOME)")


def _xdg_download_dir() -> Path | None:
    """Read ``XDG_DOWNLOAD_DIR`` from the XDG user-dirs configuration."""

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    user_dirs = config_home / "user-dirs.dirs"
    try:
        text = user_dirs.read_text(encoding="utf-8")
    except OSError:
        return None

    match = re.search(r'^XDG_DOWNLOAD_DIR="(.*)"$', text, re.MULTILINE)
    if match is None:
        return None
    value = _HOME_TOKEN.sub(str(Path.home()), match.group(1).strip())
    return Path(value).expanduser() if value else None


def default_downloads_dir() -> Path:
    """Return the user's Downloads folder, honouring XDG and env overrides."""

    override = os.environ.get("MODELSCOMPARE_DOWNLOAD_DIR")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        profile = Path(os.environ.get("USERPROFILE", Path.home()))
        return profile / "Downloads"

    if sys.platform == "darwin":
        return Path.home() / "Downloads"

    return _xdg_download_dir() or Path.home() / "Downloads"


def default_update_cache_dir() -> Path:
    """Return a per-user cache directory for downloaded release assets."""

    override = os.environ.get("MODELSCOMPARE_UPDATE_DIR")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "modelscompare" / "updates"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "modelscompare" / "updates"

    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "modelscompare" / "updates"
