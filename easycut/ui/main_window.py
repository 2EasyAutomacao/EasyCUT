"""Janela principal do EasyCUT."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDockWidget, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSlider, QSplitter, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core import csvio
from ..core import ffmpeg as ff
from ..core.ass import build_ass
from ..core.models import Cut, Project, SubtitleLine, WhisperSettings, safe_name
from ..core.timeutil import format_short, format_time, parse_time
from ..core.tools import ASSETS_DIR, find_ffmpeg, find_ffprobe
from .fonts import load_font_file
from .panels import SettingsPanel
from .preview import PreviewWidget
from .widgets import PathEdit
from .workers import FunctionThread, RenderJob, RenderQueue, TranscribeThread

PROJECT_FILTER = "Projeto EasyCUT (*.easycut);;JSON (*.json);;Todos os arquivos (*)"
VIDEO_FILTER = "Vídeos (*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.MP4 *.MOV);;Todos os arquivos (*)"
CSV_FILTER = "CSV (*.csv);;Todos os arquivos (*)"


def default_project() -> Project:
    p = Project()
    logo = ASSETS_DIR / "logo.png"
    if logo.is_file():
        p.logo.path = str(logo)
    fonts = sorted(f for f in (ASSETS_DIR / "fonts").glob("*") if f.suffix.lower() in (".ttf", ".otf"))
    if fonts:
        p.subtitle.font_file = str(fonts[0])
    movies = QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or str(Path.home())
    p.output_dir = str(Path(movies) / "EasyCUT")
    return p


def _item(text: str, editable: bool = True, align=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if align is not None:
        it.setTextAlignment(align)
    return it


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        try:
            whisper = json.loads(self.settings.value("whisper", "{}") or "{}")
        except (TypeError, ValueError):
            whisper = {}
        self.whisper = WhisperSettings.from_dict(whisper)
        self.ffmpeg_setting = str(self.settings.value("ffmpeg", "") or "")
        self.project = default_project()
        self.project_path: Path | None = None
        self.tmp = Path(tempfile.mkdtemp(prefix="easycut-"))
        self._ass_counter = 0
        self._updating = False
        self._frame_thread: FunctionThread | None = None
        self._frame_pending = False
        self._preview_thread: FunctionThread | None = None
        self._tr_thread: TranscribeThread | None = None
        self._tr_targets: dict[int, Cut] = {}
        self._shown_cut: Cut | None = None

        self.render_queue = RenderQueue(self)
        self.render_queue.log.connect(self.log)
        self.render_queue.progress.connect(self._on_progress)
        self.render_queue.finished.connect(self._on_render_finished)

        self.frame_timer = QTimer(self, singleShot=True, interval=180)
        self.frame_timer.timeout.connect(self._grab_frames)

        self._build_ui()
        self._build_actions()
        self._load_project(self.project)
        self._restore_session()

    # ================================================================ UI
    def _build_ui(self):
        self.setWindowTitle("EasyCUT")
        self.resize(1440, 900)

        # ---- coluna esquerda: fontes, cortes, legendas
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 6, 6)

        src = QGroupBox("Arquivos")
        sf = QFormLayout(src)
        self.cam1_edit = PathEdit("open", VIDEO_FILTER, "Vídeo da câmera 1")
        self.cam2_edit = PathEdit("open", VIDEO_FILTER, "Vídeo da câmera 2 (opcional)")
        self.out_edit = PathEdit("dir", "", "Pasta onde os cortes serão salvos")
        self.cam1_edit.changed.connect(lambda p: self._on_source("cam1", p))
        self.cam2_edit.changed.connect(lambda p: self._on_source("cam2", p))
        self.out_edit.changed.connect(lambda p: self._on_source("output_dir", p))
        sf.addRow("Cam 1", self.cam1_edit)
        sf.addRow("Cam 2", self.cam2_edit)
        sf.addRow("Saída", self.out_edit)
        lv.addWidget(src)

        vsplit = QSplitter(Qt.Vertical)
        cuts_box = QGroupBox("Cortes")
        cb = QVBoxLayout(cuts_box)
        self.cuts_table = QTableWidget(0, 5)
        self.cuts_table.setHorizontalHeaderLabels(["ID", "Início", "Fim", "Duração", "Leg."])
        self._setup_table(self.cuts_table, stretch=0)
        self.cuts_table.itemChanged.connect(self._on_cut_item)
        self.cuts_table.itemSelectionChanged.connect(self._on_cut_selected)
        cb.addWidget(self.cuts_table)
        row = QHBoxLayout()
        row.addWidget(self._tool("+ Corte", self.add_cut, "Adicionar corte"))
        row.addWidget(self._tool("− Remover", self.remove_cut, "Remover cortes selecionados"))
        row.addStretch(1)
        row.addWidget(self._tool("Importar CSV", self.import_cuts_csv,
                                 "Importar cortes.csv (id;start;end)"))
        cb.addLayout(row)
        vsplit.addWidget(cuts_box)

        subs_box = QGroupBox("Legendas do corte")
        sb = QVBoxLayout(subs_box)
        self.subs_table = QTableWidget(0, 3)
        self.subs_table.setHorizontalHeaderLabels(["Início", "Fim", "Texto"])
        self._setup_table(self.subs_table, stretch=2)
        self.subs_table.itemChanged.connect(self._on_sub_item)
        self.subs_table.itemSelectionChanged.connect(self._on_sub_selected)
        sb.addWidget(self.subs_table)
        row = QHBoxLayout()
        row.addWidget(self._tool("+ Linha", self.add_sub, "Adicionar legenda no tempo atual"))
        row.addWidget(self._tool("− Remover", self.remove_sub, "Remover legendas selecionadas"))
        row.addStretch(1)
        self.tr_one_btn = self._tool("🎙 Transcrever", self.transcribe_current,
                                     "Gerar legendas deste corte com Whisper local")
        self.tr_all_btn = self._tool("Todos", self.transcribe_all,
                                     "Transcrever todos os cortes com Whisper local")
        row.addWidget(self.tr_one_btn)
        row.addWidget(self.tr_all_btn)
        sb.addLayout(row)
        vsplit.addWidget(subs_box)
        vsplit.setSizes([300, 400])
        lv.addWidget(vsplit, 1)

        # ---- centro: prévia
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(6, 6, 6, 6)
        self.preview = PreviewWidget()
        self.preview.subtitleMoved.connect(self._on_sub_dragged)
        self.preview.logoMoved.connect(self._on_logo_dragged)
        self.preview.splitMoved.connect(self._on_split_dragged)
        cv.addWidget(self.preview, 1)
        trow = QHBoxLayout()
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setRange(0, 0)
        self.time_slider.valueChanged.connect(self._on_time)
        self.time_label = QLabel("0:00.00 / 0:00.00")
        self.time_label.setMinimumWidth(130)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        trow.addWidget(self.time_slider, 1)
        trow.addWidget(self.time_label)
        cv.addLayout(trow)
        brow = QHBoxLayout()
        self.real_btn = QPushButton("Prévia real (ffmpeg)")
        self.real_btn.setToolTip("Renderiza este quadro exatamente como ficará no vídeo (F5)")
        self.real_btn.clicked.connect(self.real_preview)
        self.render_one_btn = QPushButton("Renderizar corte")
        self.render_one_btn.clicked.connect(self.render_selected)
        self.render_all_btn = QPushButton("Renderizar todos")
        self.render_all_btn.setDefault(True)
        self.render_all_btn.clicked.connect(self.render_all)
        brow.addWidget(self.real_btn)
        brow.addStretch(1)
        brow.addWidget(self.render_one_btn)
        brow.addWidget(self.render_all_btn)
        cv.addLayout(brow)

        # ---- direita: configurações
        self.panel = SettingsPanel(self.project, self.whisper, self.ffmpeg_setting)
        self.panel.changed.connect(self._on_settings_changed)
        self.panel.whisperChanged.connect(self._save_whisper)
        self.panel.ffmpegChanged.connect(self._on_ffmpeg_changed)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([420, 580, 440])
        self.main_split = split
        self.setCentralWidget(split)

        # ---- log + status
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(5000)
        dock = QDockWidget("Log", self)
        dock.setObjectName("log_dock")
        dock.setWidget(self.log_view)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.log_dock = dock
        self.resizeDocks([dock], [130], Qt.Vertical)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(320)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self.cancel_jobs)
        self.cancel_btn.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)

    def _tool(self, text, slot, tip=""):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(False)
        b.clicked.connect(slot)
        return b

    def _setup_table(self, t: QTableWidget, stretch: int):
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                          | QAbstractItemView.AnyKeyPressed)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(26)
        t.setAlternatingRowColors(True)
        h = t.horizontalHeader()
        for i in range(t.columnCount()):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(stretch, QHeaderView.Stretch)

    def _build_actions(self):
        def act(text, slot, shortcut=None, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            return a

        self.act_new = act("Novo projeto", self.new_project, QKeySequence.New)
        self.act_open = act("Abrir projeto…", self.open_project, QKeySequence.Open)
        self.act_save = act("Salvar", self.save_project, QKeySequence.Save)
        self.act_save_as = act("Salvar como…", self.save_project_as, QKeySequence.SaveAs)
        self.act_imp_cuts = act("Importar cortes (CSV)…", self.import_cuts_csv)
        self.act_imp_subs = act("Importar legendas (CSV)…", self.import_subs_csv)
        self.act_export = act("Exportar CSVs…", self.export_csvs)
        self.act_quit = act("Sair", self.close, QKeySequence.Quit)
        self.act_real = act("Prévia real (ffmpeg)", self.real_preview, "F5")
        self.act_render_one = act("Renderizar corte selecionado", self.render_selected, "Ctrl+R")
        self.act_render_all = act("Renderizar todos", self.render_all, "Ctrl+Shift+R")
        self.act_open_out = act("Abrir pasta de saída", self.open_output_dir)
        self.act_tr_one = act("Transcrever corte selecionado", self.transcribe_current, "Ctrl+T")
        self.act_tr_all = act("Transcrever todos os cortes", self.transcribe_all)
        self.act_about = act("Sobre o EasyCUT", self.about)
        self.act_about.setMenuRole(QAction.AboutRole)

        mb = self.menuBar()
        m = mb.addMenu("&Arquivo")
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as, None,
                  self.act_imp_cuts, self.act_imp_subs, self.act_export, None, self.act_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Renderizar")
        for a in (self.act_real, None, self.act_render_one, self.act_render_all, None,
                  self.act_open_out):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Whisper")
        m.addAction(self.act_tr_one)
        m.addAction(self.act_tr_all)
        m = mb.addMenu("E&xibir")
        m.addAction(self.log_dock.toggleViewAction())
        m = mb.addMenu("Aj&uda")
        m.addAction(self.act_about)

        tb = self.addToolBar("Principal")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        for a in (self.act_new, self.act_open, self.act_save, None, self.act_imp_cuts,
                  self.act_imp_subs, None, self.act_open_out):
            tb.addSeparator() if a is None else tb.addAction(a)

    # =========================================================== projeto
    def _load_project(self, project: Project, path: Path | None = None):
        self.project = project
        self.project_path = path
        if project.subtitle.font_file and Path(project.subtitle.font_file).is_file():
            fam = load_font_file(project.subtitle.font_file)
            if fam:
                project.subtitle.font_family = fam
        self.cam1_edit.set_path(project.cam1)
        self.cam2_edit.set_path(project.cam2)
        self.out_edit.set_path(project.output_dir)
        self.panel.load(project)
        self.preview.set_project(project)
        self.refresh_cuts(0)
        self._set_dirty(False)
        self._grab_frames_soon()

    def _set_dirty(self, dirty: bool = True):
        name = self.project_path.stem if self.project_path else "Sem título"
        self.setWindowTitle(f"{name}[*] — EasyCUT")
        self.setWindowModified(dirty)

    def _confirm_discard(self) -> bool:
        if not self.isWindowModified():
            return True
        r = QMessageBox.question(self, "EasyCUT", "Salvar as alterações do projeto atual?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self):
        if self._confirm_discard():
            self._load_project(default_project())

    def open_project(self, path: str | None = None):
        if not path:
            if not self._confirm_discard():
                return
            path, _ = QFileDialog.getOpenFileName(self, "Abrir projeto", self._last_dir(),
                                                  PROJECT_FILTER)
            if not path:
                return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            project = Project.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as e:
            QMessageBox.critical(self, "Abrir projeto", f"Não foi possível abrir o projeto:\n{e}")
            return
        self._load_project(project, Path(path))
        self.settings.setValue("last_project", str(path))
        self.log(f"Projeto aberto: {path}")

    def save_project(self) -> bool:
        if not self.project_path:
            return self.save_project_as()
        try:
            self.project_path.write_text(json.dumps(self.project.to_dict(), ensure_ascii=False,
                                                    indent=2), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Salvar", f"Não foi possível salvar:\n{e}")
            return False
        self.settings.setValue("last_project", str(self.project_path))
        self._set_dirty(False)
        self.statusBar().showMessage(f"Projeto salvo em {self.project_path}", 4000)
        return True

    def save_project_as(self) -> bool:
        start = str(self.project_path or Path(self._last_dir()) / "projeto.easycut")
        path, _ = QFileDialog.getSaveFileName(self, "Salvar projeto", start, PROJECT_FILTER)
        if not path:
            return False
        if not Path(path).suffix:
            path += ".easycut"
        self.project_path = Path(path)
        return self.save_project()

    def _last_dir(self) -> str:
        for p in (self.project_path, self.project.cam1):
            if p and Path(p).parent.is_dir():
                return str(Path(p).parent)
        return str(Path.home())

    def _restore_session(self):
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("window_state")
        if state is not None:
            self.restoreState(state)
        last = self.settings.value("last_project")
        if last and Path(str(last)).is_file():
            self.open_project(str(last))

    # ============================================================ fontes
    def _on_source(self, attr: str, path: str):
        setattr(self.project, attr, path)
        self._set_dirty()
        if attr == "output_dir":
            return
        self.panel.refresh("layout")
        self.preview.clear_real()
        if attr == "cam1" and path and not self.project.cuts and Path(path).is_file():
            dur = ff.probe_duration(find_ffprobe(self.ffmpeg()), path)
            if dur:
                self.project.cuts.append(Cut("corte_01", 0.0, round(min(dur, 60.0), 3)))
                self.refresh_cuts(0)
                self.log(f"Vídeo com {format_short(dur)} — criado um corte inicial.")
        self._grab_frames_soon()

    def ffmpeg(self, quiet: bool = True) -> str | None:
        exe = find_ffmpeg(self.ffmpeg_setting)
        if not exe and not quiet:
            QMessageBox.critical(
                self, "FFmpeg não encontrado",
                "Instale o FFmpeg (macOS: brew install ffmpeg · Linux: gerenciador de pacotes) "
                "ou indique o executável na aba Exportar.")
        return exe

    def _on_ffmpeg_changed(self, path: str):
        self.ffmpeg_setting = path
        self.settings.setValue("ffmpeg", path)
        self._grab_frames_soon()

    def _save_whisper(self):
        self.settings.setValue("whisper", json.dumps(self.whisper.__dict__))

    def _check_sources(self) -> bool:
        p = self.project
        problems = []
        if not p.cam1:
            problems.append("Escolha o vídeo da Cam 1.")
        elif not Path(p.cam1).is_file():
            problems.append(f"Cam 1 não encontrada: {p.cam1}")
        if p.cam2 and not Path(p.cam2).is_file():
            problems.append(f"Cam 2 não encontrada: {p.cam2}")
        if problems:
            QMessageBox.warning(self, "EasyCUT", "\n".join(problems))
            return False
        return True

    # ============================================================ cortes
    def current_cut(self) -> Cut | None:
        r = self.cuts_table.currentRow()
        return self.project.cuts[r] if 0 <= r < len(self.project.cuts) else None

    def refresh_cuts(self, select: int | None = None):
        t = self.cuts_table
        if select is None:
            select = t.currentRow()
        self._updating = True
        t.blockSignals(True)
        t.setRowCount(len(self.project.cuts))
        right = Qt.AlignRight | Qt.AlignVCenter
        for r, c in enumerate(self.project.cuts):
            bad = c.duration <= 0
            t.setItem(r, 0, _item(c.id))
            t.setItem(r, 1, _item(format_time(c.start), align=right))
            t.setItem(r, 2, _item(format_time(c.end), align=right))
            dur = _item("inválido" if bad else f"{c.duration:.1f}s", False, right)
            if bad:
                dur.setForeground(Qt.red)
            t.setItem(r, 3, dur)
            t.setItem(r, 4, _item(str(len(c.subtitles)), False, right))
        if self.project.cuts:
            t.setCurrentCell(min(max(select, 0), len(self.project.cuts) - 1), 0)
        t.blockSignals(False)
        self._updating = False
        self._on_cut_selected()

    def _on_cut_selected(self):
        if self._updating:
            return
        cut = self.current_cut()
        self.refresh_subs()
        if cut is self._shown_cut and cut is not None:
            # mesmo corte (só atualizou a tabela): mantém o tempo atual
            self.time_slider.setMaximum(int(cut.duration * 100))
            self.preview.update()
            self._update_time_label()
            self._grab_frames_soon()
            return
        self._shown_cut = cut
        self.preview.set_cut(cut)
        self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, int((cut.duration if cut else 0) * 100))
        self.time_slider.setValue(0)
        self.time_slider.blockSignals(False)
        self._update_time_label()
        self._grab_frames_soon()

    def _on_cut_item(self, item: QTableWidgetItem):
        if self._updating:
            return
        r, col = item.row(), item.column()
        cut = self.project.cuts[r]
        try:
            if col == 0:
                if not item.text().strip():
                    raise ValueError("o ID não pode ficar vazio")
                cut.id = item.text().strip()
            elif col == 1:
                cut.start = parse_time(item.text())
            elif col == 2:
                cut.end = parse_time(item.text())
            self._set_dirty()
        except ValueError as e:
            self.statusBar().showMessage(f"Valor inválido: {e}", 5000)
        QTimer.singleShot(0, lambda: self.refresh_cuts(r))

    def add_cut(self):
        cuts = self.project.cuts
        start = cuts[-1].end if cuts else 0.0
        n = len(cuts) + 1
        ids = {c.id for c in cuts}
        while f"corte_{n:02}" in ids:
            n += 1
        cuts.append(Cut(f"corte_{n:02}", start, start + 30.0))
        self._set_dirty()
        self.refresh_cuts(len(cuts) - 1)
        self.cuts_table.editItem(self.cuts_table.item(len(cuts) - 1, 1))

    def remove_cut(self):
        rows = sorted({i.row() for i in self.cuts_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if any(self.project.cuts[r].subtitles for r in rows):
            if QMessageBox.question(self, "Remover", f"Remover {len(rows)} corte(s) e suas "
                                    "legendas?") != QMessageBox.Yes:
                return
        for r in rows:
            del self.project.cuts[r]
        self._set_dirty()
        self.refresh_cuts(min(rows))

    # ========================================================== legendas
    def refresh_subs(self, select: int | None = None):
        cut = self.current_cut()
        t = self.subs_table
        self._updating = True
        t.blockSignals(True)
        subs = cut.subtitles if cut else []
        t.setRowCount(len(subs))
        right = Qt.AlignRight | Qt.AlignVCenter
        for r, s in enumerate(subs):
            t.setItem(r, 0, _item(format_short(s.start), align=right))
            t.setItem(r, 1, _item(format_short(s.end), align=right))
            txt = _item(s.text)
            if s.words:
                txt.setToolTip("Tempos por palavra do Whisper")
            t.setItem(r, 2, txt)
        if select is not None and 0 <= select < len(subs):
            t.setCurrentCell(select, 2)
        t.blockSignals(False)
        self._updating = False
        self.subs_table.setEnabled(cut is not None)

    def _on_sub_item(self, item: QTableWidgetItem):
        cut = self.current_cut()
        if self._updating or not cut:
            return
        line = cut.subtitles[item.row()]
        try:
            if item.column() == 0:
                line.start = parse_time(item.text())
            elif item.column() == 1:
                line.end = parse_time(item.text())
            else:
                line.text = " ".join(item.text().split())
            self._set_dirty()
        except ValueError as e:
            self.statusBar().showMessage(f"Valor inválido: {e}", 5000)
        cut.subtitles.sort(key=lambda s: s.start)
        row = cut.subtitles.index(line)
        QTimer.singleShot(0, lambda: (self.refresh_subs(row), self.preview.update()))

    def _on_sub_selected(self):
        cut = self.current_cut()
        r = self.subs_table.currentRow()
        if self._updating or not cut or not 0 <= r < len(cut.subtitles):
            return
        line = cut.subtitles[r]
        self.time_slider.setValue(int(round((line.start + min(0.5, (line.end - line.start) / 2)) * 100)))

    def add_sub(self):
        cut = self.current_cut()
        if not cut:
            return
        t = self.preview.time
        line = SubtitleLine(round(t, 2), round(min(cut.duration, t + 2.0), 2), "Nova legenda")
        cut.subtitles.append(line)
        cut.subtitles.sort(key=lambda s: s.start)
        row = cut.subtitles.index(line)
        self._set_dirty()
        self.refresh_subs(row)
        self.refresh_cuts()
        self.subs_table.setCurrentCell(row, 2)
        self.subs_table.editItem(self.subs_table.item(row, 2))

    def remove_sub(self):
        cut = self.current_cut()
        rows = sorted({i.row() for i in self.subs_table.selectedIndexes()}, reverse=True)
        if not cut or not rows:
            return
        for r in rows:
            del cut.subtitles[r]
        self._set_dirty()
        self.refresh_subs(min(rows[-1], len(cut.subtitles) - 1))
        self.refresh_cuts()

    # ============================================================ prévia
    def _on_time(self, v: int):
        self.preview.set_time(v / 100)
        self._update_time_label()
        self._grab_frames_soon()

    def _update_time_label(self):
        cut = self.current_cut()
        self.time_label.setText(f"{format_short(self.preview.time)} / "
                                f"{format_short(cut.duration if cut else 0)}")

    def _grab_frames_soon(self):
        self.frame_timer.start()

    def _grab_frames(self):
        if self._frame_thread and self._frame_thread.isRunning():
            self._frame_pending = True
            return
        exe = self.ffmpeg()
        if not exe:
            return
        cut = self.current_cut()
        t = (cut.start if cut else 0.0) + self.preview.time
        sources = {1: self.project.cam1, 2: self.project.cam2}

        def job():
            out = {}
            for cam, path in sources.items():
                img = None
                if path and Path(path).is_file():
                    try:
                        img = QImage.fromData(ff.grab_frame(exe, path, t))
                    except Exception:  # noqa: BLE001 — quadro opcional
                        img = None
                out[cam] = img if img is not None and not img.isNull() else None
            return out

        self._frame_pending = False
        self._frame_thread = FunctionThread(job, self)
        self._frame_thread.done.connect(self.preview.set_frames)
        self._frame_thread.finished.connect(self._frame_done)
        self._frame_thread.start()

    def _frame_done(self):
        if self._frame_pending:
            self._grab_frames()

    def _fonts_dir(self) -> Path | None:
        st = self.project.subtitle
        if not st.font_file or not Path(st.font_file).is_file():
            return None
        d = self.tmp / "fonts"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        shutil.copy2(st.font_file, d / ("font" + Path(st.font_file).suffix.lower()))
        return d

    def _write_ass(self, cut: Cut) -> Path | None:
        if not self.project.subtitle.enabled:
            return None
        self._ass_counter += 1
        path = self.tmp / f"sub_{self._ass_counter}.ass"
        W, H = self.project.layout.size()
        path.write_text(build_ass(cut, self.project.subtitle, W, H), encoding="utf-8")
        return path

    def real_preview(self):
        cut = self.current_cut()
        exe = self.ffmpeg(quiet=False)
        if not cut or not exe or not self._check_sources():
            return
        if self._preview_thread and self._preview_thread.isRunning():
            return
        out = self.tmp / "preview.png"
        out.unlink(missing_ok=True)
        args = ff.build_render_command(exe, self.project, cut, self._write_ass(cut),
                                       self._fonts_dir(), out, preview_at=self.preview.time)

        def job():
            res = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if res.returncode != 0 or not out.is_file():
                raise RuntimeError(res.stderr.strip()[-1500:] or "ffmpeg falhou")
            return QImage(str(out))

        self.real_btn.setEnabled(False)
        self.statusBar().showMessage("Gerando prévia real…")
        self._preview_thread = FunctionThread(job, self)
        self._preview_thread.done.connect(self.preview.set_real_frame)
        self._preview_thread.failed.connect(lambda e: self.log(f"✖ Prévia falhou: {e}"))
        self._preview_thread.finished.connect(lambda: (self.real_btn.setEnabled(True),
                                                       self.statusBar().clearMessage()))
        self._preview_thread.start()

    # ======================================================= configurações
    def _on_settings_changed(self, section: str):
        self.preview.clear_real()
        self._set_dirty()

    def _on_sub_dragged(self, x: float, y: float):
        st = self.project.subtitle
        st.pos_x, st.pos_y = round(x, 4), round(y, 4)
        self.panel.refresh("subtitle")
        self.preview.update()
        self._set_dirty()

    def _on_logo_dragged(self, x: int, y: int):
        self.project.logo.x, self.project.logo.y = x, y
        self.panel.refresh("logo")
        self.preview.update()
        self._set_dirty()

    def _on_split_dragged(self, frac: float):
        self.project.layout.split = frac
        self.panel.refresh("layout")
        self.preview.update()
        self._set_dirty()

    # ============================================================ render
    def _busy(self) -> bool:
        busy = self.render_queue.running or bool(self._tr_thread and self._tr_thread.isRunning())
        if busy:
            self.statusBar().showMessage("Aguarde a tarefa atual terminar (ou cancele).", 4000)
        return busy

    def _set_running(self, running: bool):
        for w in (self.render_one_btn, self.render_all_btn, self.tr_one_btn, self.tr_all_btn):
            w.setEnabled(not running)
        for a in (self.act_render_one, self.act_render_all, self.act_tr_one, self.act_tr_all):
            a.setEnabled(not running)
        self.progress.setVisible(running)
        self.cancel_btn.setVisible(running)
        if running:
            self.progress.setValue(0)
            self.log_dock.show()

    def _on_progress(self, pct: int, label: str):
        self.progress.setValue(pct)
        self.progress.setFormat(f"{label}  %p%")

    def render_selected(self):
        cut = self.current_cut()
        if cut:
            self._start_render([cut])

    def render_all(self):
        self._start_render(list(self.project.cuts))

    def _start_render(self, cuts: list[Cut]):
        if self._busy() or not cuts:
            return
        exe = self.ffmpeg(quiet=False)
        if not exe or not self._check_sources():
            return
        out_dir = Path(self.project.output_dir or Path.home() / "EasyCUT").expanduser()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Saída", f"Não foi possível criar a pasta de saída:\n{e}")
            return
        fonts = self._fonts_dir()
        jobs, used = [], set()
        for cut in cuts:
            if cut.duration <= 0:
                self.log(f"Pulando corte inválido: {cut.id}")
                continue
            name, n = safe_name(cut.id), 2
            stem = name
            while stem.casefold() in used:
                stem, n = f"{name}_{n}", n + 1
            used.add(stem.casefold())
            out = out_dir / f"{stem}.mp4"
            args = ff.build_render_command(exe, self.project, cut, self._write_ass(cut), fonts, out)
            jobs.append(RenderJob(cut.id, args, cut.duration, out))
        if not jobs:
            return
        self.log(f"— Renderizando {len(jobs)} corte(s) em {out_dir}")
        self._set_running(True)
        self.render_queue.start(jobs)

    def _on_render_finished(self, ok: int, total: int, cancelled: bool):
        self._set_running(False)
        msg = (f"Render cancelado ({ok}/{total} prontos)." if cancelled
               else f"Render concluído: {ok}/{total} corte(s).")
        self.log(msg)
        self.statusBar().showMessage(msg, 8000)
        if ok and not cancelled:
            QApplication.alert(self)

    def cancel_jobs(self):
        if self.render_queue.running:
            self.render_queue.cancel()
        if self._tr_thread and self._tr_thread.isRunning():
            self._tr_thread.cancel()
            self.statusBar().showMessage("Cancelando transcrição após o trecho atual…")

    def open_output_dir(self):
        d = Path(self.project.output_dir).expanduser()
        if d.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        else:
            self.statusBar().showMessage("A pasta de saída ainda não existe.", 4000)

    # ======================================================== transcrição
    def transcribe_current(self):
        cut = self.current_cut()
        if cut:
            self._start_transcription([cut])

    def transcribe_all(self):
        self._start_transcription(list(self.project.cuts))

    def _start_transcription(self, cuts: list[Cut]):
        if self._busy() or not cuts:
            return
        exe = self.ffmpeg(quiet=False)
        if not exe or not self._check_sources():
            return
        from ..core import transcribe
        try:
            transcribe.resolve_backend(self.whisper)
        except transcribe.TranscriptionError as e:
            QMessageBox.information(self, "Whisper", f"{e}\n\nConfigure na aba Whisper.")
            self.panel.setCurrentIndex(3)
            return
        cuts = [c for c in cuts if c.duration > 0]
        with_subs = [c for c in cuts if c.subtitles]
        if with_subs and QMessageBox.question(
                self, "Whisper", f"{len(with_subs)} corte(s) já têm legendas, que serão "
                "substituídas. Continuar?") != QMessageBox.Yes:
            return
        audio = ff.cam_path(self.project, ff.audio_camera(self.project))
        self._tr_targets = {i: c for i, c in enumerate(cuts)}
        jobs = [(i, c.id, c.start, c.duration) for i, c in enumerate(cuts)]
        th = TranscribeThread(exe, audio, jobs, copy.deepcopy(self.whisper), self.tmp, self)
        th.log.connect(self.log)
        th.progress.connect(self._on_progress)
        th.cut_done.connect(self._on_transcribed)
        th.failed.connect(self._on_transcribe_failed)
        th.finished.connect(lambda: self._set_running(False))
        self._tr_thread = th
        self._set_running(True)
        th.start()

    def _on_transcribed(self, key: int, lines: list):
        cut = self._tr_targets.get(key)
        if cut is None or cut not in self.project.cuts:
            return
        cut.subtitles = lines
        self._set_dirty()
        self.refresh_cuts()

    def _on_transcribe_failed(self, msg: str):
        self.log(f"✖ Whisper: {msg}")
        QMessageBox.warning(self, "Whisper", msg[:2000])

    # ================================================================ CSV
    def import_cuts_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar cortes", self._last_dir(), CSV_FILTER)
        if not path:
            return
        try:
            cuts = csvio.import_cuts(Path(path))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Importar cortes", str(e))
            return
        if self.project.cuts and QMessageBox.question(
                self, "Importar cortes", "Substituir os cortes atuais?") != QMessageBox.Yes:
            return
        self.project.cuts = cuts
        self._set_dirty()
        self.refresh_cuts(0)
        self.log(f"{len(cuts)} corte(s) importados de {path}")
        sibling = Path(path).with_name("legendas.csv")
        if sibling.is_file() and QMessageBox.question(
                self, "Importar legendas", f"Importar também {sibling.name}?") == QMessageBox.Yes:
            self._import_subs(sibling)

    def import_subs_csv(self):
        if not self.project.cuts:
            QMessageBox.information(self, "Importar legendas", "Importe ou crie os cortes primeiro.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Importar legendas", self._last_dir(),
                                              CSV_FILTER)
        if path:
            self._import_subs(Path(path))

    def _import_subs(self, path: Path):
        try:
            ok, skipped = csvio.import_subtitles(path, self.project.cuts)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Importar legendas", str(e))
            return
        self._set_dirty()
        self.refresh_cuts()
        self.log(f"{ok} legenda(s) importadas de {path}"
                 + (f" ({skipped} sem corte correspondente)" if skipped else ""))

    def export_csvs(self):
        d = QFileDialog.getExistingDirectory(self, "Exportar cortes.csv e legendas.csv",
                                             self._last_dir())
        if not d:
            return
        try:
            csvio.export_cuts(Path(d) / "cortes.csv", self.project.cuts)
            csvio.export_subtitles(Path(d) / "legendas.csv", self.project.cuts)
        except OSError as e:
            QMessageBox.warning(self, "Exportar", str(e))
            return
        self.log(f"CSVs exportados em {d}")

    # ============================================================== misc
    def log(self, text: str):
        self.log_view.appendPlainText(text)

    def about(self):
        QMessageBox.about(
            self, "Sobre o EasyCUT",
            f"<h3>EasyCUT {__version__}</h3>"
            "<p>Fábrica de cortes verticais com legendas karaokê.</p>"
            "<p>Qt (PySide6) + FFmpeg/libass · Whisper local opcional.</p>")

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.cancel_jobs()
        for th in (self._tr_thread, self._frame_thread, self._preview_thread):
            if th and th.isRunning():
                th.wait(3000)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        shutil.rmtree(self.tmp, ignore_errors=True)
        e.accept()
