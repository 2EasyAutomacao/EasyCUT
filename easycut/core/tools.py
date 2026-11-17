"""Localização de executáveis externos (ffmpeg, whisper.cpp) em Linux e macOS."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = ROOT_DIR / "Assets"

# Apps abertos pelo Finder/Dock no macOS não herdam o PATH do shell,
# então procuramos também nos lugares usuais do Homebrew/MacPorts.
_EXTRA_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/bin",
    "/snap/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / "bin"),
    str(ROOT_DIR / "ffmpeg" / "bin"),
    str(ROOT_DIR / "bin"),
]


def _is_exe(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


def find_tool(names: list[str], configured: str = "") -> str | None:
    if configured:
        p = Path(configured).expanduser()
        if _is_exe(p):
            return str(p)
    if os.name == "nt":
        names = [n + ".exe" for n in names] + names
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    for d in _EXTRA_DIRS:
        for n in names:
            p = Path(d) / n
            if _is_exe(p):
                return str(p)
    return None


def find_ffmpeg(configured: str = "") -> str | None:
    return find_tool(["ffmpeg"], configured)


def find_ffprobe(ffmpeg: str | None) -> str | None:
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
        if _is_exe(sibling):
            return str(sibling)
    return find_tool(["ffprobe"])


def find_whisper_cpp(configured: str = "") -> str | None:
    return find_tool(["whisper-cli", "whisper-cpp", "whisper.cpp"], configured)


def ffmpeg_info(ffmpeg: str) -> tuple[str, bool]:
    """(versão, tem libass?)"""
    try:
        ver = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True,
                             text=True, timeout=10).stdout.splitlines()
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                                 text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "", False
    version = ver[0].replace("ffmpeg version ", "").split(" Copyright")[0] if ver else "?"
    has_ass = any(line.split()[1:2] == ["ass"] for line in filters.splitlines() if line.strip())
    return version, has_ass
