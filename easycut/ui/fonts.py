"""Fontes de legenda: carregamento de arquivos e equivalência de tamanho com o libass."""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF

from ..core.models import SubtitleStyle

_loaded: dict[str, str | None] = {}


def load_font_file(path: str) -> str | None:
    """Registra o arquivo de fonte no Qt e devolve o nome da família."""
    if path in _loaded:
        return _loaded[path]
    fid = QFontDatabase.addApplicationFont(path)
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    _loaded[path] = fams[0] if fams else None
    return _loaded[path]


def subtitle_font(style: SubtitleStyle) -> QFont:
    """QFont com o mesmo tamanho visual do libass.

    No ASS, Fontsize é a altura total (ascendente + descendente); no Qt o
    pixelSize é o 'em'. Fazemos a conversão medindo a própria fonte.
    """
    f = QFont(style.font_family)
    f.setBold(style.bold)
    f.setItalic(style.italic)
    f.setPixelSize(100)
    fm = QFontMetricsF(f)
    h = fm.ascent() + fm.descent()
    f.setPixelSize(max(1, round(style.font_size * 100 / h)) if h > 0 else style.font_size)
    return f
