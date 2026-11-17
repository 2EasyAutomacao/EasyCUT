"""Modelo de dados do projeto (serializável em JSON)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

# (rótulo, largura, altura)
ASPECT_PRESETS = [
    ("9:16 — Reels / Shorts / TikTok", 1080, 1920),
    ("4:5 — Feed Instagram", 1080, 1350),
    ("1:1 — Quadrado", 1080, 1080),
    ("3:4 — Vertical", 1080, 1440),
    ("16:9 — YouTube / horizontal", 1920, 1080),
]

LAYOUT_MODES = [
    ("stack", "Empilhado (vertical)"),
    ("side", "Lado a lado"),
    ("single", "Câmera única"),
]

# Alinhamento ASS (\an, teclado numérico) → rótulo
ANCHORS = [
    (7, "Superior esquerdo"), (8, "Superior centro"), (9, "Superior direito"),
    (4, "Meio esquerdo"), (5, "Centro"), (6, "Meio direito"),
    (1, "Inferior esquerdo"), (2, "Inferior centro"), (3, "Inferior direito"),
]


def even(value: float, minimum: int = 2) -> int:
    return max(minimum, int(round(value)) // 2 * 2)


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip() or "corte"


def _pick(cls, data) -> dict:
    if not isinstance(data, dict):
        return {}
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class Word:
    start: float
    end: float
    text: str

    @classmethod
    def from_dict(cls, d: dict) -> "Word":
        return cls(float(d["start"]), float(d["end"]), str(d["text"]))


@dataclass
class SubtitleLine:
    """Tempos relativos ao início do corte. `words` vem do Whisper (opcional)."""
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "SubtitleLine":
        return cls(
            float(d["start"]), float(d["end"]), str(d.get("text", "")),
            [Word.from_dict(w) for w in d.get("words", [])],
        )


@dataclass
class Cut:
    id: str
    start: float
    end: float
    subtitles: list[SubtitleLine] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @classmethod
    def from_dict(cls, d: dict) -> "Cut":
        return cls(
            str(d["id"]), float(d["start"]), float(d["end"]),
            [SubtitleLine.from_dict(s) for s in d.get("subtitles", [])],
        )


@dataclass
class LayoutSettings:
    width: int = 1080
    height: int = 1920
    mode: str = "stack"        # stack | side | single
    split: float = 0.5         # fração ocupada pela primeira câmera
    swap: bool = False         # Cam 2 primeiro
    audio_source: int = 1      # 1 ou 2

    def size(self) -> tuple[int, int]:
        return even(self.width, 16), even(self.height, 16)


@dataclass
class SubtitleStyle:
    enabled: bool = True
    font_file: str = ""        # .ttf/.otf opcional (senão usa fonte do sistema)
    font_family: str = "Bazinga!"
    font_size: int = 72        # em pixels do vídeo final
    bold: bool = True
    italic: bool = True
    uppercase: bool = False
    text_color: str = "#FFFFFF"
    highlight_color: str = "#8A35FF"
    outline_color: str = "#000000"
    outline: float = 4.0
    shadow: float = 1.0
    karaoke: bool = True
    pos_x: float = 0.5         # 0..1 da largura
    pos_y: float = 0.5         # 0..1 da altura
    anchor: int = 5            # \an do ASS
    max_width: float = 0.85    # fração da largura antes de quebrar linha


@dataclass
class LogoSettings:
    enabled: bool = True
    path: str = ""
    width: int = 250
    opacity: float = 0.88
    x: int = 40
    y: int = 80


@dataclass
class RenderSettings:
    crf: int = 20
    preset: str = "veryfast"
    audio_bitrate: str = "192k"


@dataclass
class WhisperSettings:
    """Fica nas preferências da máquina (QSettings), não no projeto."""
    backend: str = "auto"      # auto | faster-whisper | openai-whisper | mlx-whisper | whisper.cpp
    model: str = "small"
    language: str = "pt"       # "auto" = detectar
    device: str = "auto"       # auto | cpu | cuda
    cpp_bin: str = ""
    cpp_model: str = ""
    max_words: int = 4
    max_chars: int = 24
    max_gap: float = 0.7
    max_duration: float = 3.5
    remove_punct: bool = False

    @classmethod
    def from_dict(cls, d) -> "WhisperSettings":
        return cls(**_pick(cls, d))


@dataclass
class Project:
    cam1: str = ""
    cam2: str = ""
    output_dir: str = ""
    cuts: list[Cut] = field(default_factory=list)
    layout: LayoutSettings = field(default_factory=LayoutSettings)
    subtitle: SubtitleStyle = field(default_factory=SubtitleStyle)
    logo: LogoSettings = field(default_factory=LogoSettings)
    render: RenderSettings = field(default_factory=RenderSettings)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["version"] = 1
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            cam1=str(d.get("cam1", "")),
            cam2=str(d.get("cam2", "")),
            output_dir=str(d.get("output_dir", "")),
            cuts=[Cut.from_dict(c) for c in d.get("cuts", [])],
            layout=LayoutSettings(**_pick(LayoutSettings, d.get("layout"))),
            subtitle=SubtitleStyle(**_pick(SubtitleStyle, d.get("subtitle"))),
            logo=LogoSettings(**_pick(LogoSettings, d.get("logo"))),
            render=RenderSettings(**_pick(RenderSettings, d.get("render"))),
        )
