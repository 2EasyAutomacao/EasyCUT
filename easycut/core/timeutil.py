"""Conversão e formatação de tempos."""
from __future__ import annotations

import re

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# Mesmo formato aceito pelo TimeSpan.Parse do .NET: [d.]h:m[:s[.fração]]
_TIMESPAN = re.compile(r"(?:(\d+)\.)?(\d+):(\d+)(?::(\d+)(?:[.,](\d+))?)?")


def parse_timespan(value) -> float:
    """Formato dos CSVs antigos (compatível com TimeSpan): "1:23" = 1h23min."""
    text = str(value).strip()
    if _NUMBER.fullmatch(text):
        return float(text.replace(",", "."))
    m = _TIMESPAN.fullmatch(text)
    if not m:
        raise ValueError(f"Tempo inválido: {text!r}")
    d, h, mi, s, frac = m.groups()
    seconds = int(d or 0) * 86400 + int(h) * 3600 + int(mi) * 60 + int(s or 0)
    if frac:
        seconds += float("0." + frac)
    return float(seconds)


def parse_time(value) -> float:
    """Formato amigável para edição: "83.5", "1:23.5" (m:s) ou "0:01:23.5" (h:m:s)."""
    text = str(value).strip().replace(",", ".")
    if _NUMBER.fullmatch(text):
        return float(text)
    parts = text.split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"Tempo inválido: {value!r}")
    if not all(p.isdigit() for p in parts[:-1]) or not re.fullmatch(r"\d+(?:\.\d+)?", parts[-1]):
        raise ValueError(f"Tempo inválido: {value!r}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def format_time(seconds: float) -> str:
    """0:01:23.450 — lido corretamente pelos dois parsers."""
    ms = round(max(0.0, float(seconds)) * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h}:{m:02}:{s:02}.{ms:03}"


def format_short(seconds: float) -> str:
    """1:23.45 — para rótulos."""
    cs = round(max(0.0, float(seconds)) * 100)
    m, rem = divmod(cs, 6000)
    s, cs = divmod(rem, 100)
    return f"{m}:{s:02}.{cs:02}"


def format_ass_time(seconds: float) -> str:
    cs = round(max(0.0, float(seconds)) * 100)
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"
