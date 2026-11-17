"""Importação/exportação dos CSVs no formato antigo (id;start;end[;text])."""
from __future__ import annotations

import csv
from pathlib import Path

from .models import Cut, SubtitleLine
from .timeutil import format_time, parse_timespan


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        return [
            {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f, delimiter=delimiter)
        ]


def import_cuts(path: Path) -> list[Cut]:
    cuts = []
    for n, row in enumerate(_rows(path), start=2):
        try:
            cuts.append(Cut(row["id"], parse_timespan(row["start"]), parse_timespan(row["end"])))
        except (KeyError, ValueError) as e:
            raise ValueError(f"{Path(path).name}, linha {n}: {e}") from e
    return cuts


def import_subtitles(path: Path, cuts: list[Cut]) -> tuple[int, int]:
    """Anexa as legendas aos cortes pelo id. Retorna (importadas, ignoradas)."""
    by_id = {c.id.strip().casefold(): c for c in cuts}
    touched, ok, skipped = set(), 0, 0
    for n, row in enumerate(_rows(path), start=2):
        try:
            cut = by_id.get(row["id"].casefold())
            line = SubtitleLine(parse_timespan(row["start"]), parse_timespan(row["end"]),
                                row.get("text", ""))
        except (KeyError, ValueError) as e:
            raise ValueError(f"{Path(path).name}, linha {n}: {e}") from e
        if cut is None:
            skipped += 1
            continue
        if id(cut) not in touched:
            cut.subtitles = []
            touched.add(id(cut))
        cut.subtitles.append(line)
        ok += 1
    for c in cuts:
        c.subtitles.sort(key=lambda s: s.start)
    return ok, skipped


def export_cuts(path: Path, cuts: list[Cut]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "start", "end"])
        for c in cuts:
            w.writerow([c.id, format_time(c.start), format_time(c.end)])


def export_subtitles(path: Path, cuts: list[Cut]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "start", "end", "text"])
        for c in cuts:
            for s in c.subtitles:
                w.writerow([c.id, format_time(s.start), format_time(s.end), s.text])
