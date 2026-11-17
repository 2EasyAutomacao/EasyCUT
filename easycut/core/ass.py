"""Geração das legendas .ass (base branca + camada karaokê por cima)."""
from __future__ import annotations

from .models import Cut, SubtitleLine, SubtitleStyle
from .timeutil import format_ass_time


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = str(hex_color).lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def escape_ass_text(text: str) -> str:
    t = str(text).replace("\r", " ").replace("\n", " ")
    return t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").strip()


def clean_text(text: str, style: SubtitleStyle) -> str:
    t = " ".join(str(text).split())
    return t.upper() if style.uppercase else t


def word_timings(line: SubtitleLine) -> list[tuple[float, float, str]]:
    """(início, fim, palavra) de cada palavra, relativos ao corte.

    Usa os tempos do Whisper quando o número de palavras confere; senão distribui
    a duração da linha proporcionalmente ao tamanho de cada palavra.
    """
    words = str(line.text).split()
    if not words:
        return []
    ls, le = line.start, max(line.end, line.start)
    if line.words and len(line.words) == len(words):
        out, prev = [], ls
        for w, txt in zip(line.words, words):
            s = min(max(w.start, prev), le)
            e = min(max(w.end, s), le)
            out.append((s, e, txt))
            prev = s
        return out
    weights = [len(w) + 1 for w in words]
    total = sum(weights)
    t, out = ls, []
    for w, wt in zip(words, weights):
        d = (le - ls) * wt / total
        out.append((t, t + d, w))
        t += d
    return out


def _karaoke_text(timings: list[tuple[float, float, str]], start: float) -> str:
    """Texto com \\kf por palavra; pausas entre palavras viram \\k no espaço."""
    def cs(t: float) -> int:
        return round((t - start) * 100)

    parts, cursor, n = [], 0, len(timings)
    for i, (_ws, we, word) in enumerate(timings):
        fill = max(1, cs(we) - cursor)
        parts.append(f"{{\\kf{fill}}}{escape_ass_text(word)}")
        cursor += fill
        if i < n - 1:
            gap = cs(timings[i + 1][0]) - cursor
            if gap > 0:
                parts.append(f"{{\\k{gap}}} ")
                cursor += gap
            else:
                parts.append(" ")
    return "".join(parts)


def build_ass(cut: Cut, style: SubtitleStyle, width: int, height: int) -> str:
    font = style.font_family.replace(",", " ") or "Arial"
    bold = -1 if style.bold else 0
    italic = -1 if style.italic else 0
    margin = max(0, round(width * (1 - style.max_width) / 2))
    common = (f"{bold},{italic},0,0,100,100,0,0,1,{style.outline:g},{style.shadow:g},"
              f"{style.anchor},{margin},{margin},0,1")
    transparent = "&HFF000000"
    out = [
        "[Script Info]",
        f"Title: {escape_ass_text(cut.id)}",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Base,{font},{style.font_size},{ass_color(style.text_color)},{ass_color(style.text_color)},"
        f"{ass_color(style.outline_color)},{ass_color(style.outline_color)},{common}",
        # Mesmas métricas (contorno/sombra) da base, mas invisíveis: só o preenchimento aparece.
        f"Style: Highlight,{font},{style.font_size},{ass_color(style.highlight_color)},{transparent},"
        f"{transparent},{transparent},{common}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    pos = f"{{\\an{style.anchor}\\pos({round(style.pos_x * width)},{round(style.pos_y * height)})}}"
    duration = cut.duration
    for line in sorted(cut.subtitles, key=lambda s: s.start):
        ls, le = max(0.0, line.start), min(line.end, duration)
        text = clean_text(line.text, style)
        if le <= ls or not text:
            continue
        timings = word_timings(SubtitleLine(ls, le, text, line.words))
        base = escape_ass_text(" ".join(w for _, _, w in timings))
        out.append(f"Dialogue: 0,{format_ass_time(ls)},{format_ass_time(le)},Base,,0,0,0,,{pos}{base}")
        if style.karaoke:
            ks = timings[0][0]
            out.append(
                f"Dialogue: 1,{format_ass_time(ks)},{format_ass_time(le)},Highlight,,0,0,0,,"
                f"{pos}{_karaoke_text(timings, ks)}"
            )
    return "\r\n".join(out) + "\r\n"
