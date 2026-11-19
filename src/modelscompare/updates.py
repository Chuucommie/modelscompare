"""GitHub Releases discovery and safe self-update support.

The updater deliberately keeps all user data outside the installation
directory.  Preferences live in the platform configuration folder, so
replacing the binary never touches favourites, filters, or export settings.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from modelscompare import __version__
from modelscompare.paths import default_update_cache_dir

DEFAULT_REPO = "Chuucommie/modelscompare"
RELEASES_URL = "https://api.github.com/repos/{repo}/releases/latest"
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
_BINARY_NAMES = ("modelscompare", "modelscompare.exe", "ModelsCompare", "ModelsCompare.exe")
_ALL_ARCH_TOKENS = {"x86_64", "x64", "amd64", "arm64", "aarch64", "i386", "i686"}


class UpdateError(RuntimeError):
    """Raised when a release cannot be checked, downloaded, or installed."""


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    url: str
    size: int = 0
    digest: str = ""
    content_type: str = ""


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    current: str
    latest: str
    tag: str
    html_url: str
    notes: str = ""
    published_at: str = ""
    assets: tuple[ReleaseAsset, ...] = ()
    is_newer: bool = False

    @property
    def version_label(self) -> str:
        return self.latest.lstrip("vV") or self.latest


@dataclass(frozen=True, slots=True)
class UpdateResult:
    scheduled: bool
    message: str
    binary: Path | None = None
    restart_required: bool = False


_VERSION_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)(?:[-+]([0-9A-Za-z.-]+))?$")


def _version_key(version: str) -> tuple[tuple[int, ...], int]:
    """Return a sortable key; stable releases sort after pre-releases."""

    normalized = version.strip().lstrip("vV")
    match = _VERSION_PATTERN.match(normalized)
    if match is None:
        return ((), 0)
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return (tuple(parts), 0 if match.group(2) else 1)


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return _version_key(candidate) > _version_key(current)


def _release_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"modelscompare/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def check_for_update(
    current_version: str = __version__,
    *,
    repo: str = DEFAULT_REPO,
    timeout: float = 4.0,
    session: Any = None,
) -> UpdateInfo | None:
    """Return the latest release, or ``None`` when the repository is empty."""

    repository = os.environ.get("MODELSCOMPARE_REPO", repo).strip().strip("/")
    client = session or requests
    try:
        response = client.get(
            RELEASES_URL.format(repo=repository),
            headers=_release_headers(),
            timeout=timeout,
        )
        if getattr(response, "status_code", 200) == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError) as error:
        raise UpdateError(f"Impossibile controllare le release: {error}") from error

    if not isinstance(payload, dict):
        raise UpdateError("La risposta del repository non contiene una release valida.")

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("La release non contiene un tag di versione.")

    assets: list[ReleaseAsset] = []
    raw_assets = payload.get("assets")
    if isinstance(raw_assets, list):
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("browser_download_url") or "").strip()
            if not name or not url:
                continue
            assets.append(
                ReleaseAsset(
                    name=name,
                    url=url,
                    size=int(item.get("size") or 0),
                    digest=str(item.get("digest") or ""),
                    content_type=str(item.get("content_type") or ""),
                )
            )

    return UpdateInfo(
        current=current_version,
        latest=tag,
        tag=tag,
        html_url=str(payload.get("html_url") or ""),
        notes=str(payload.get("body") or ""),
        published_at=str(payload.get("published_at") or ""),
        assets=tuple(assets),
        is_newer=is_newer_version(tag, current_version),
    )


def _platform_tokens(system: str, machine: str) -> tuple[set[str], set[str]]:
    system = system.casefold()
    machine = machine.casefold()
    if system == "windows":
        os_tokens = {"windows", "win32", "win64", "win"}
    elif system == "darwin":
        os_tokens = {"macos", "mac", "darwin", "osx", "universal2"}
    else:
        os_tokens = {"linux"}

    if machine in {"x86_64", "amd64", "x64"}:
        arch_tokens = {"x86_64", "x64", "amd64"}
    elif machine in {"arm64", "aarch64"}:
        arch_tokens = {"arm64", "aarch64"}
    else:
        arch_tokens = {machine} if machine else set()
    return os_tokens, arch_tokens


def select_asset(
    assets: Iterable[ReleaseAsset],
    *,
    system: str | None = None,
    machine: str | None = None,
) -> ReleaseAsset | None:
    """Pick the best release asset for the current operating system."""

    resolved_system = (system or platform.system()).casefold()
    os_tokens, arch_tokens = _platform_tokens(resolved_system, machine or platform.machine())
    best: ReleaseAsset | None = None
    best_score = -1

    for asset in assets:
        name = asset.name.casefold()
        if name.endswith((".sha256", ".sha256sum", ".sig", ".asc", ".txt")):
            continue

        is_windows_exe = resolved_system == "windows" and name.endswith(".exe")
        if not any(token in name for token in os_tokens) and not is_windows_exe:
            continue

        asset_arch_tokens = {token for token in _ALL_ARCH_TOKENS if token in name}
        if asset_arch_tokens and not asset_arch_tokens & arch_tokens:
            continue

        score = 10
        if any(token in name for token in arch_tokens):
            score += 5
        if resolved_system == "windows":
            if name.endswith(".exe"):
                score += 5
            elif name.endswith(".zip"):
                score += 3
        else:
            if name.endswith((".tar.gz", ".tgz")):
                score += 5
            elif name.endswith(".zip"):
                score += 2
        if "source" in name or "src" in name:
            score -= 6

        if score > best_score:
            best = asset
            best_score = score

    return best


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_digest(asset: ReleaseAsset) -> str:
    digest = asset.digest.strip()
    if digest.casefold().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().casefold()
    if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return digest.casefold()
    return ""


def _checksum_asset(asset: ReleaseAsset, assets: Iterable[ReleaseAsset]) -> ReleaseAsset | None:
    wanted = {
        f"{asset.name}.sha256".casefold(),
        f"{asset.name}.sha256sum".casefold(),
        "sha256sums.txt",
        "checksums.txt",
    }
    for candidate in assets:
        if candidate.name.casefold() in wanted:
            return candidate
    return None


def _remote_checksum(
    checksum_asset: ReleaseAsset,
    target_name: str,
    *,
    timeout: float,
    session: Any = None,
) -> str:
    client = session or requests
    try:
        response = client.get(
            checksum_asset.url,
            headers=_release_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        text = response.text
    except requests.RequestException as error:
        raise UpdateError(f"Checksum non scaricabile: {error}") from error

    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == target_name:
            candidate = parts[0].casefold()
            if re.fullmatch(r"[0-9a-f]{64}", candidate):
                return candidate
    return ""


def download_asset(
    asset: ReleaseAsset,
    destination_dir: Path,
    *,
    timeout: float = 45.0,
    session: Any = None,
    checksum_assets: Iterable[ReleaseAsset] = (),
) -> Path:
    """Download an asset atomically and verify its SHA-256 when published."""

    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise UpdateError(f"Impossibile creare la cache degli aggiornamenti: {error}") from error
    safe_name = Path(asset.name).name
    if not safe_name or safe_name in {".", ".."}:
        raise UpdateError("Il nome del pacchetto di aggiornamento non è valido.")
    target = destination_dir / safe_name
    partial = target.with_name(target.name + ".part")
    client = session or requests
    total = 0

    try:
        with client.get(
            asset.url,
            headers=_release_headers(),
            timeout=timeout,
            stream=True,
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as file:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise UpdateError("Il pacchetto di aggiornamento è troppo grande.")
                    file.write(chunk)
    except requests.RequestException as error:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Download dell'aggiornamento non riuscito: {error}") from error
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Impossibile scrivere l'aggiornamento: {error}") from error

    if asset.size and total != asset.size:
        partial.unlink(missing_ok=True)
        raise UpdateError("Il pacchetto scaricato ha una dimensione inattesa.")

    expected = _expected_digest(asset)
    checksum_asset = _checksum_asset(asset, checksum_assets)
    if not expected and checksum_asset is not None:
        try:
            expected = _remote_checksum(
                checksum_asset,
                target.name,
                timeout=timeout,
                session=session,
            )
        except UpdateError:
            partial.unlink(missing_ok=True)
            raise
    if expected and _sha256(partial) != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError("Il checksum SHA-256 dell'aggiornamento non corrisponde.")

    try:
        partial.replace(target)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Impossibile salvare l'aggiornamento: {error}") from error
    return target


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise UpdateError("Il pacchetto contiene un percorso non sicuro.")
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise UpdateError("Il pacchetto contiene un collegamento simbolico non sicuro.")
        bundle.extractall(destination)


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise UpdateError("Il pacchetto contiene un percorso non sicuro.")
            if member.issym() or member.islnk():
                raise UpdateError("Il pacchetto contiene collegamenti non sicuri.")
            if not (member.isfile() or member.isdir()):
                continue
        try:
            bundle.extractall(destination, filter="data")
        except TypeError:
            bundle.extractall(destination)


def _find_binary(root: Path) -> Path:
    candidates: list[Path] = []
    for name in _BINARY_NAMES:
        candidates.extend(root.rglob(name))
    files = [path for path in candidates if path.is_file()]
    if files:
        return max(files, key=lambda path: path.stat().st_size)
    for path in root.rglob("*"):
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise UpdateError("L'archivio non contiene un eseguibile riconoscibile.")


def extract_update(archive: Path, destination: Path) -> Path:
    """Extract an update archive and return the executable inside it."""

    destination.mkdir(parents=True, exist_ok=True)
    name = archive.name.casefold()
    if name.endswith(".zip"):
        _safe_extract_zip(archive, destination)
    elif name.endswith((".tar.gz", ".tgz")):
        _safe_extract_tar(archive, destination)
    else:
        return archive
    return _find_binary(destination)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _windows_updater_script(
    new_binary: Path,
    current_binary: Path,
    pid: int,
    arguments: Sequence[str] = (),
) -> Path:
    script = Path(tempfile.gettempdir()) / f"modelscompare-update-{pid}.bat"
    restart = subprocess.list2cmdline(list(arguments)) if arguments else ""
    start_command = f'start "" "{current_binary}"'
    if restart:
        start_command += f" {restart}"
    script.write_text(
        "\r\n".join(
            (
                "@echo off",
                "setlocal",
                ":wait",
                f'copy /Y "{new_binary}" "{current_binary}" >NUL 2>&1',
                "if errorlevel 1 (",
                "  >NUL timeout /t 1 /nobreak",
                "  goto wait",
                ")",
                start_command,
                'del "%~f0"',
            )
        )
        + "\r\n",
        encoding="utf-8",
    )
    return script


def _posix_updater_script(
    new_binary: Path,
    current_binary: Path,
    pid: int,
    arguments: Sequence[str] = (),
) -> Path:
    script = Path(tempfile.gettempdir()) / f"modelscompare-update-{pid}.sh"
    restart = " ".join(shlex.quote(argument) for argument in arguments)
    exec_line = f"exec {shlex.quote(str(current_binary))}"
    if restart:
        exec_line += f" {restart}"
    script.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done",
                f"cp {shlex.quote(str(new_binary))} {shlex.quote(str(current_binary))}",
                f"chmod +x {shlex.quote(str(current_binary))}",
                exec_line,
                'rm -f -- "$0"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return script


def schedule_restart(
    new_binary: Path,
    current_binary: Path,
    *,
    pid: int | None = None,
    arguments: Sequence[str] = (),
) -> None:
    """Replace the running binary after this process exits, then relaunch it."""

    process_id = pid or os.getpid()
    if os.name == "nt":
        script = _windows_updater_script(new_binary, current_binary, process_id, arguments)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            close_fds=True,
            creationflags=flags,
        )
        return

    script = _posix_updater_script(new_binary, current_binary, process_id, arguments)
    subprocess.Popen(
        ["/bin/sh", str(script)],
        close_fds=True,
        start_new_session=True,
    )


def install_update(
    info: UpdateInfo,
    *,
    asset: ReleaseAsset | None = None,
    cache_dir: Path | None = None,
    current_executable: Path | None = None,
    system: str | None = None,
    machine: str | None = None,
    restart_args: Sequence[str] = (),
) -> UpdateResult:
    """Download and schedule installation of a newer release.

    Automatic replacement is only attempted for frozen (standalone) builds.
    Source and pip installations keep working; they receive instructions
    instead of having their Python environment modified behind their back.
    """

    if not info.is_newer:
        return UpdateResult(False, "ModelsCompare è già aggiornato.", restart_required=False)

    if not is_frozen():
        return UpdateResult(
            False,
            "Installazione da sorgente: aggiorna con "
            "`python -m pip install --upgrade modelscompare` oppure scarica "
            f"la release da {info.html_url or 'GitHub Releases'}.",
            restart_required=False,
        )

    selected = asset or select_asset(
        info.assets,
        system=system,
        machine=machine,
    )
    if selected is None:
        return UpdateResult(
            False,
            f"Nessun pacchetto compatibile nella release {info.version_label}.",
            restart_required=False,
        )

    executable = Path(current_executable or sys.executable).resolve()
    if not os.access(executable.parent, os.W_OK):
        return UpdateResult(
            False,
            "La cartella dell'eseguibile non è scrivibile. Scarica la nuova "
            f"release manualmente da {info.html_url or 'GitHub Releases'}.",
            restart_required=False,
        )

    cache = cache_dir or default_update_cache_dir()
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "_", info.version_label).strip("._") or "update"
    asset_dir = cache / safe_version
    archive = download_asset(
        selected,
        asset_dir,
        checksum_assets=info.assets,
    )
    try:
        new_binary = extract_update(archive, asset_dir / "unpacked")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as error:
        raise UpdateError(f"Impossibile estrarre l'aggiornamento: {error}") from error
    if not os.access(new_binary, os.X_OK) and os.name != "nt":
        new_binary.chmod(0o755)

    try:
        schedule_restart(new_binary, executable, arguments=restart_args)
    except OSError as error:
        raise UpdateError(f"Impossibile avviare il programma di aggiornamento: {error}") from error
    return UpdateResult(
        True,
        f"Aggiornamento {info.version_label} pronto. ModelsCompare verrà riavviato.",
        binary=new_binary,
        restart_required=True,
    )
