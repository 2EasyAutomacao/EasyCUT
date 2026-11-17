"""Painel de configurações (abas): formato, legendas, logo, Whisper e exportação."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QImageReader
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..core import transcribe
from ..core.ffmpeg import effective_mode, layout_regions
from ..core.models import ANCHORS, ASPECT_PRESETS, LAYOUT_MODES, Project, WhisperSettings
from ..core.tools import find_ffmpeg, find_whisper_cpp, ffmpeg_info
from .fonts import load_font_file
from .widgets import ColorButton, PathEdit

FONT_FILTER = "Fontes (*.ttf *.otf *.ttc *.TTF *.OTF);;Todos os arquivos (*)"
IMAGE_FILTER = "Imagens (*.png *.jpg *.jpeg *.webp *.PNG *.JPG);;Todos os arquivos (*)"
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"]


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


def _spin(lo, hi, step=1, suffix="", decimals=None):
    w = QDoubleSpinBox() if decimals is not None else QSpinBox()
    if decimals is not None:
        w.setDecimals(decimals)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    if suffix:
        w.setSuffix(suffix)
    w.setKeyboardTracking(False)
    return w


class SettingsPanel(QTabWidget):
    changed = Signal(str)          # seção do projeto alterada
    whisperChanged = Signal()
    ffmpegChanged = Signal(str)

    def __init__(self, project: Project, whisper: WhisperSettings, ffmpeg_setting: str,
                 parent=None):
        super().__init__(parent)
        self.project = project
        self.whisper = whisper
        self.ffmpeg_setting = ffmpeg_setting
        self._bindings: list[tuple[str, callable]] = []
        self._loading = False
        self.addTab(_scroll(self._format_tab()), "Formato")
        self.addTab(_scroll(self._subtitle_tab()), "Legendas")
        self.addTab(_scroll(self._logo_tab()), "Logo")
        self.addTab(_scroll(self._whisper_tab()), "Whisper")
        self.addTab(_scroll(self._export_tab()), "Exportar")
        self.setDocumentMode(True)
        self.setElideMode(Qt.ElideNone)
        self.setMinimumWidth(380)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
        self.load(project)

    # ---- ligação widget ↔ modelo -----------------------------------------
    def _target(self, section: str):
        return self.whisper if section == "whisper" else getattr(self.project, section)

    def _bind(self, w, section: str, attr: str, scale: float = 1.0):
        def to_widget():
            v = getattr(self._target(section), attr)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(v) * scale)
            elif isinstance(w, QSpinBox):
                w.setValue(int(round(float(v) * scale)))
            elif isinstance(w, QComboBox) and w.isEditable():
                w.setCurrentText(str(v))
            elif isinstance(w, QComboBox):
                i = w.findData(v)
                w.setCurrentIndex(max(0, i))
            elif isinstance(w, ColorButton):
                w.set_color(v)
            elif isinstance(w, PathEdit):
                w.set_path(v)

        def from_widget(*_):
            if self._loading:
                return
            if isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, QDoubleSpinBox):
                v = w.value() / scale
            elif isinstance(w, QSpinBox):
                v = w.value() / scale if scale != 1 else w.value()
            elif isinstance(w, QComboBox) and w.isEditable():
                v = w.currentText().strip()
            elif isinstance(w, QComboBox):
                v = w.currentData()
            elif isinstance(w, ColorButton):
                v = w.color()
            else:
                v = w.path()
            setattr(self._target(section), attr, v)
            self._emit(section)

        if isinstance(w, QCheckBox):
            w.toggled.connect(from_widget)
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.valueChanged.connect(from_widget)
        elif isinstance(w, QComboBox) and w.isEditable():
            w.currentTextChanged.connect(from_widget)
        elif isinstance(w, QComboBox):
            w.currentIndexChanged.connect(from_widget)
        elif isinstance(w, ColorButton):
            w.colorChanged.connect(from_widget)
        elif isinstance(w, PathEdit):
            w.changed.connect(from_widget)
        self._bindings.append((section, to_widget))
        return w

    def _emit(self, section: str):
        if section == "whisper":
            self._refresh_whisper_status()
            self.whisperChanged.emit()
            return
        if section == "layout":
            self._sync_aspect_combo()
            self._refresh_layout_hint()
        self.changed.emit(section)

    def load(self, project: Project):
        self.project = project
        self.refresh()

    def refresh(self, section: str | None = None):
        self._loading = True
        try:
            for sec, fn in self._bindings:
                if section is None or sec == section:
                    fn()
            self._sync_aspect_combo()
            self._sync_font_controls()
            self._refresh_layout_hint()
        finally:
            self._loading = False

    # ---- Formato --------------------------------------------------------
    def _format_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        g = QGroupBox("Proporção do vídeo")
        f = QFormLayout(g)
        self.aspect = QComboBox()
        for label, _w, _h in ASPECT_PRESETS:
            self.aspect.addItem(label)
        self.aspect.addItem("Personalizado")
        self.aspect.currentIndexChanged.connect(self._on_aspect)
        f.addRow("Formato", self.aspect)
        size = QHBoxLayout()
        self.width_spin = self._bind(_spin(144, 4096, 2, " px"), "layout", "width")
        self.height_spin = self._bind(_spin(144, 4096, 2, " px"), "layout", "height")
        size.addWidget(self.width_spin)
        size.addWidget(QLabel("×"))
        size.addWidget(self.height_spin)
        f.addRow("Resolução", size)
        v.addWidget(g)

        g = QGroupBox("Câmeras")
        f = QFormLayout(g)
        mode = QComboBox()
        for key, label in LAYOUT_MODES:
            mode.addItem(label, key)
        f.addRow("Layout", self._bind(mode, "layout", "mode"))
        self.split_spin = self._bind(_spin(15, 85, 1, " %"), "layout", "split", 100)
        self.split_spin.setToolTip("Quanto do quadro a primeira câmera ocupa. "
                                   "Também dá para arrastar a divisória na prévia.")
        f.addRow("Divisão", self.split_spin)
        f.addRow("", self._bind(QCheckBox("Inverter ordem das câmeras"), "layout", "swap"))
        audio = QComboBox()
        audio.addItem("Cam 1", 1)
        audio.addItem("Cam 2", 2)
        f.addRow("Áudio de", self._bind(audio, "layout", "audio_source"))
        self.layout_hint = QLabel()
        self.layout_hint.setWordWrap(True)
        self.layout_hint.setStyleSheet("color: gray;")
        f.addRow(self.layout_hint)
        v.addWidget(g)
        v.addStretch(1)
        return page

    def _on_aspect(self, _i):
        if self._loading:
            return
        i = self.aspect.currentIndex()
        if 0 <= i < len(ASPECT_PRESETS):
            _label, self.project.layout.width, self.project.layout.height = ASPECT_PRESETS[i]
            self.refresh("layout")
            self._emit("layout")

    def _sync_aspect_combo(self):
        lay = self.project.layout
        size = (int(lay.width), int(lay.height))
        i = next((n for n, (_l, w, h) in enumerate(ASPECT_PRESETS) if (w, h) == size), -1)
        self.aspect.blockSignals(True)
        self.aspect.setCurrentIndex(i if i >= 0 else self.aspect.count() - 1)
        self.aspect.blockSignals(False)

    def _refresh_layout_hint(self):
        lay = self.project.layout
        mode = effective_mode(lay, bool(self.project.cam2))
        parts = []
        if mode != lay.mode:
            parts.append("Sem Cam 2: usando câmera única.")
        if mode != "single":
            regs = layout_regions(lay, bool(self.project.cam2))
            parts.append(" · ".join(f"Cam {c}: {w}×{h}" for c, _x, _y, w, h in regs))
        self.split_spin.setEnabled(mode != "single")
        self.layout_hint.setText(" ".join(parts))

    # ---- Legendas -------------------------------------------------------
    def _subtitle_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(self._bind(QCheckBox("Gravar legendas no vídeo"), "subtitle", "enabled"))

        g = QGroupBox("Posição")
        grid = QGridLayout(g)
        self.pos_x = self._bind(_spin(0, 100, 0.5, " %", 1), "subtitle", "pos_x", 100)
        self.pos_y = self._bind(_spin(0, 100, 0.5, " %", 1), "subtitle", "pos_y", 100)
        grid.addWidget(QLabel("Horizontal"), 0, 0)
        grid.addWidget(self.pos_x, 0, 1)
        grid.addWidget(QLabel("Vertical"), 1, 0)
        grid.addWidget(self.pos_y, 1, 1)
        anchor = QComboBox()
        for an, label in ANCHORS:
            anchor.addItem(label, an)
        anchor.setToolTip("Qual ponto do bloco de texto fica na posição escolhida.")
        grid.addWidget(QLabel("Âncora"), 2, 0)
        grid.addWidget(self._bind(anchor, "subtitle", "anchor"), 2, 1)
        quick = QGridLayout()
        for i, (label, y) in enumerate((("Topo", 0.15), ("Centro", 0.5),
                                        ("Na divisão", None), ("Base", 0.8))):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, y=y: self._quick_pos(y))
            quick.addWidget(b, i // 2, i % 2)
        grid.addLayout(quick, 3, 0, 1, 2)
        tip = QLabel("Dica: arraste a legenda direto na prévia.")
        tip.setStyleSheet("color: gray;")
        tip.setWordWrap(True)
        grid.addWidget(tip, 4, 0, 1, 2)
        v.addWidget(g)

        g = QGroupBox("Tamanho e quebra")
        f = QFormLayout(g)
        f.addRow("Tamanho", self._bind(_spin(8, 400, 2, " px"), "subtitle", "font_size"))
        f.addRow("Largura máx.", self._bind(_spin(20, 100, 1, " %"), "subtitle", "max_width", 100))
        v.addWidget(g)

        g = QGroupBox("Fonte")
        f = QFormLayout(g)
        self.font_file = PathEdit("open", FONT_FILTER, "Arquivo .ttf/.otf (opcional)")
        self.font_file.changed.connect(self._on_font_file)
        f.addRow("Arquivo", self.font_file)
        self.font_combo = QFontComboBox()
        self.font_combo.setMinimumWidth(120)
        self.font_combo.currentFontChanged.connect(self._on_font_family)
        f.addRow("Família", self.font_combo)
        styles = QGridLayout()
        styles.addWidget(self._bind(QCheckBox("Negrito"), "subtitle", "bold"), 0, 0)
        styles.addWidget(self._bind(QCheckBox("Itálico"), "subtitle", "italic"), 0, 1)
        styles.addWidget(self._bind(QCheckBox("Tudo em MAIÚSCULAS"), "subtitle", "uppercase"), 1, 0, 1, 2)
        f.addRow(styles)
        v.addWidget(g)

        g = QGroupBox("Cores e efeito")
        f = QFormLayout(g)
        f.addRow("Texto", self._bind(ColorButton(), "subtitle", "text_color"))
        f.addRow("Destaque karaokê", self._bind(ColorButton(), "subtitle", "highlight_color"))
        f.addRow("Contorno", self._bind(ColorButton(), "subtitle", "outline_color"))
        f.addRow("Espessura contorno", self._bind(_spin(0, 30, 0.5, " px", 1), "subtitle", "outline"))
        f.addRow("Sombra", self._bind(_spin(0, 30, 0.5, " px", 1), "subtitle", "shadow"))
        karaoke = self._bind(QCheckBox("Efeito karaokê"), "subtitle", "karaoke")
        karaoke.setToolTip("Preenche cada palavra com a cor de destaque no tempo em que é falada.")
        f.addRow(karaoke)
        v.addWidget(g)
        v.addStretch(1)
        return page

    def _quick_pos(self, y: float | None):
        st = self.project.subtitle
        if y is None:
            regs = layout_regions(self.project.layout, bool(self.project.cam2))
            H = self.project.layout.size()[1]
            y = regs[1][2] / H if len(regs) == 2 and regs[1][2] > 0 else 0.5
            st.anchor = 5
        st.pos_x, st.pos_y = 0.5, y
        self.refresh("subtitle")
        self._emit("subtitle")

    def _on_font_file(self, path: str):
        st = self.project.subtitle
        st.font_file = path
        if path:
            fam = load_font_file(path) if Path(path).is_file() else None
            if fam:
                st.font_family = fam
        self._sync_font_controls()
        self._emit("subtitle")

    def _on_font_family(self, font: QFont):
        if self._loading or self.project.subtitle.font_file:
            return
        self.project.subtitle.font_family = font.family()
        self._emit("subtitle")

    def _sync_font_controls(self):
        st = self.project.subtitle
        self.font_file.set_path(st.font_file)
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(st.font_family))
        self.font_combo.blockSignals(False)
        self.font_combo.setEnabled(not st.font_file)
        self.font_combo.setToolTip(f"Definida pelo arquivo: {st.font_family}" if st.font_file else "")

    # ---- Logo -----------------------------------------------------------
    def _logo_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox("Logo")
        f = QFormLayout(g)
        f.addRow("", self._bind(QCheckBox("Mostrar logo"), "logo", "enabled"))
        f.addRow("Imagem", self._bind(PathEdit("open", IMAGE_FILTER, "PNG com transparência"),
                                      "logo", "path"))
        f.addRow("Largura", self._bind(_spin(8, 4000, 10, " px"), "logo", "width"))
        f.addRow("Opacidade", self._bind(_spin(0, 100, 5, " %"), "logo", "opacity", 100))
        f.addRow("X", self._bind(_spin(-4000, 8000, 5, " px"), "logo", "x"))
        f.addRow("Y", self._bind(_spin(-4000, 8000, 5, " px"), "logo", "y"))
        corners = QGridLayout()
        for i, (label, cx, cy) in enumerate((("↖ Sup. esq.", 0, 0), ("↗ Sup. dir.", 1, 0),
                                             ("↙ Inf. esq.", 0, 1), ("↘ Inf. dir.", 1, 1))):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, cx=cx, cy=cy: self._logo_corner(cx, cy))
            corners.addWidget(b, i // 2, i % 2)
        f.addRow("Cantos", corners)
        tip = QLabel("Dica: arraste o logo direto na prévia.")
        tip.setStyleSheet("color: gray;")
        tip.setWordWrap(True)
        f.addRow(tip)
        v.addWidget(g)
        v.addStretch(1)
        return page

    def _logo_corner(self, cx: int, cy: int):
        lg = self.project.logo
        W, H = self.project.layout.size()
        size = QImageReader(lg.path).size() if lg.path else None
        h = lg.width * size.height() / size.width() if size and size.width() > 0 else lg.width
        margin = round(min(W, H) * 0.04)
        lg.x = round(W - lg.width - margin) if cx else margin
        lg.y = round(H - h - margin) if cy else margin
        self.refresh("logo")
        self._emit("logo")

    # ---- Whisper --------------------------------------------------------
    def _whisper_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        intro = QLabel("Transcrição automática com Whisper rodando <b>localmente</b> (opcional). "
                       "Gera legendas com o tempo exato de cada palavra para o karaokê.")
        intro.setWordWrap(True)
        v.addWidget(intro)

        g = QGroupBox("Motor")
        f = QFormLayout(g)
        backend = QComboBox()
        backend.addItem("Automático", "auto")
        for key, label in transcribe.BACKENDS:
            backend.addItem(label, key)
        f.addRow("Back-end", self._bind(backend, "whisper", "backend"))
        model = QComboBox()
        model.setEditable(True)
        model.addItems(transcribe.MODELS)
        model.setToolTip("Nome do modelo, caminho local ou repositório do Hugging Face.")
        f.addRow("Modelo", self._bind(model, "whisper", "model"))
        lang = QComboBox()
        lang.setEditable(True)
        lang.addItems(["pt", "en", "es", "fr", "it", "de", "auto"])
        f.addRow("Idioma", self._bind(lang, "whisper", "language"))
        device = QComboBox()
        for key, label in (("auto", "Automático"), ("cpu", "CPU"), ("cuda", "GPU NVIDIA (CUDA)")):
            device.addItem(label, key)
        f.addRow("Dispositivo", self._bind(device, "whisper", "device"))
        v.addWidget(g)

        g = QGroupBox("whisper.cpp")
        f = QFormLayout(g)
        f.addRow("Executável", self._bind(PathEdit("open", "", "whisper-cli (auto)"),
                                          "whisper", "cpp_bin"))
        f.addRow("Modelo ggml", self._bind(
            PathEdit("open", "Modelos ggml (*.bin);;Todos (*)", "ggml-small.bin"),
            "whisper", "cpp_model"))
        v.addWidget(g)

        g = QGroupBox("Divisão das legendas")
        f = QFormLayout(g)
        f.addRow("Máx. palavras por linha", self._bind(_spin(1, 20), "whisper", "max_words"))
        f.addRow("Máx. caracteres", self._bind(_spin(5, 120), "whisper", "max_chars"))
        f.addRow("Pausa que quebra linha", self._bind(_spin(0.1, 5, 0.1, " s", 1), "whisper", "max_gap"))
        f.addRow("Duração máx. da linha", self._bind(_spin(0.5, 15, 0.5, " s", 1),
                                                     "whisper", "max_duration"))
        f.addRow("", self._bind(QCheckBox("Remover pontuação"), "whisper", "remove_punct"))
        v.addWidget(g)

        g = QGroupBox("Disponibilidade")
        gl = QVBoxLayout(g)
        self.whisper_status = QLabel()
        self.whisper_status.setWordWrap(True)
        self.whisper_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        gl.addWidget(self.whisper_status)
        again = QPushButton("Verificar novamente")
        again.clicked.connect(self._refresh_whisper_status)
        gl.addWidget(again, 0, Qt.AlignLeft)
        v.addWidget(g)
        v.addStretch(1)
        self._refresh_whisper_status()
        return page

    def _refresh_whisper_status(self):
        st = transcribe.backend_status(self.whisper)
        rows = []
        for key, label in transcribe.BACKENDS:
            reason = st.get(key)
            if reason is None:
                extra = f" — {find_whisper_cpp(self.whisper.cpp_bin)}" if key == "whisper.cpp" else ""
                rows.append(f"<span style='color:#2e9d4f'>✔</span> {label}{extra}")
            else:
                rows.append(f"<span style='color:gray'>✖ {label} — <code>{reason}</code></span>")
        try:
            chosen = transcribe.resolve_backend(self.whisper)
            rows.append(f"<br><b>Será usado:</b> {chosen}")
        except transcribe.TranscriptionError as e:
            rows.append(f"<br><b>Indisponível:</b> {str(e).splitlines()[0]}")
        self.whisper_status.setText("<br>".join(rows))

    # ---- Exportar -------------------------------------------------------
    def _export_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox("Vídeo")
        f = QFormLayout(g)
        crf = self._bind(_spin(0, 51), "render", "crf")
        crf.setToolTip("Qualidade do x264: menor = melhor/maior arquivo. 18–23 é o usual.")
        f.addRow("Qualidade (CRF)", crf)
        preset = QComboBox()
        for p in PRESETS:
            preset.addItem(p, p)
        f.addRow("Velocidade (preset)", self._bind(preset, "render", "preset"))
        abr = QComboBox()
        for b in ("128k", "160k", "192k", "256k", "320k"):
            abr.addItem(b, b)
        f.addRow("Áudio AAC", self._bind(abr, "render", "audio_bitrate"))
        v.addWidget(g)

        g = QGroupBox("FFmpeg")
        f = QFormLayout(g)
        self.ffmpeg_edit = PathEdit("open", "", "detectar automaticamente")
        self.ffmpeg_edit.set_path(self.ffmpeg_setting)
        self.ffmpeg_edit.changed.connect(self._on_ffmpeg)
        f.addRow("Executável", self.ffmpeg_edit)
        self.ffmpeg_label = QLabel()
        self.ffmpeg_label.setWordWrap(True)
        self.ffmpeg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow(self.ffmpeg_label)
        v.addWidget(g)
        v.addStretch(1)
        self._refresh_ffmpeg_label()
        return page

    def _on_ffmpeg(self, path: str):
        self.ffmpeg_setting = path
        self._refresh_ffmpeg_label()
        self.ffmpegChanged.emit(path)

    def _refresh_ffmpeg_label(self):
        exe = find_ffmpeg(self.ffmpeg_setting)
        if not exe:
            self.ffmpeg_label.setText(
                "<span style='color:#c0392b'>FFmpeg não encontrado.</span> Instale com "
                "<code>brew install ffmpeg</code> (macOS) ou pelo gerenciador de pacotes (Linux).")
            return
        version, has_ass = ffmpeg_info(exe)
        warn = "" if has_ass else ("<br><span style='color:#c0392b'>Este ffmpeg não tem libass: "
                                   "as legendas não serão gravadas.</span>")
        self.ffmpeg_label.setText(f"{exe}<br>versão {version}{warn}")
