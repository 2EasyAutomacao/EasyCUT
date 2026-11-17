"""Tarefas em segundo plano: render (QProcess), transcrição e funções genéricas (QThread)."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QThread, Signal

from ..core import ffmpeg as ff
from ..core import transcribe
from ..core.models import WhisperSettings


class FunctionThread(QThread):
    """Executa `fn()` fora da thread da interface."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # noqa: BLE001 — repassado à interface
            self.failed.emit(str(e) or type(e).__name__)


@dataclass
class RenderJob:
    name: str
    args: list[str]
    duration: float
    output: Path


class RenderQueue(QObject):
    """Fila de renders do ffmpeg com progresso via `-progress pipe:1`."""

    log = Signal(str)
    progress = Signal(int, str)
    finished = Signal(int, int, bool)  # ok, total, cancelado

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.jobs: list[RenderJob] = []
        self.index = -1
        self.ok = 0
        self.cancelled = False
        self._buf = ""

    @property
    def running(self) -> bool:
        return 0 <= self.index < len(self.jobs)

    def start(self, jobs: list[RenderJob]):
        self.jobs, self.index, self.ok, self.cancelled = list(jobs), -1, 0, False
        self._next()

    def cancel(self):
        self.cancelled = True
        if self.proc:
            self.proc.kill()

    def _next(self):
        self.index += 1
        if self.cancelled or self.index >= len(self.jobs):
            total = len(self.jobs)
            self.jobs, self.index = [], -1
            self.finished.emit(self.ok, total, self.cancelled)
            return
        job = self.jobs[self.index]
        self.log.emit(f"▶ Renderizando “{job.name}” → {job.output}")
        self.progress.emit(int(self.index * 100 / len(self.jobs)), job.name)
        self._buf = ""
        proc = QProcess(self)
        proc.setProgram(job.args[0])
        proc.setArguments(job.args[1:])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self.proc = proc
        proc.start()

    def _on_stdout(self):
        job = self.jobs[self.index]
        self._buf += bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and val.isdigit():
                frac = min(1.0, int(val) / 1e6 / max(job.duration, 1e-3))
                overall = (self.index + frac) / len(self.jobs)
                self.progress.emit(int(overall * 100), f"{job.name} — {int(frac * 100)}%")

    def _on_stderr(self):
        text = bytes(self.proc.readAllStandardError()).decode(errors="replace").strip()
        if text:
            self.log.emit(text)

    def _cleanup(self, ok: bool):
        job = self.jobs[self.index]
        if ok:
            self.ok += 1
            self.log.emit(f"✔ Finalizado: {job.output}")
        else:
            if not self.cancelled:
                self.log.emit(f"✖ Erro ao renderizar “{job.name}”")
            job.output.unlink(missing_ok=True)
        if self.proc:
            self.proc.deleteLater()
            self.proc = None
        self._next()

    def _on_finished(self, code, status):
        self._cleanup(status == QProcess.NormalExit and code == 0)

    def _on_error(self, err):
        if err == QProcess.FailedToStart:
            self.log.emit(f"✖ Não foi possível iniciar o ffmpeg: {self.proc.errorString()}")
            self._cleanup(False)


class TranscribeThread(QThread):
    log = Signal(str)
    progress = Signal(int, str)
    cut_done = Signal(int, object)   # chave do job, list[SubtitleLine]
    failed = Signal(str)

    def __init__(self, ffmpeg: str, audio_path: str, jobs: list[tuple[int, str, float, float]],
                 settings: WhisperSettings, workdir: Path, parent=None):
        super().__init__(parent)
        self.ffmpeg = ffmpeg
        self.audio_path = audio_path
        self.jobs = jobs
        self.settings = settings
        self.workdir = workdir
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            backend = transcribe.resolve_backend(self.settings)
            self.log.emit(f"Whisper: usando {backend} (modelo {self.settings.model})")
            for n, (key, name, start, dur) in enumerate(self.jobs):
                if self._cancel.is_set():
                    raise transcribe.Cancelled
                self.progress.emit(int(n * 100 / len(self.jobs)), f"Transcrevendo “{name}”")
                self.log.emit(f"🎙 Transcrevendo “{name}”…")
                wav = self.workdir / f"whisper_{n}.wav"
                ff.extract_audio(self.ffmpeg, self.audio_path, start, dur, wav)
                words = transcribe.transcribe_file(wav, self.settings, backend,
                                                   self.log.emit, self._cancel)
                words = [w for w in words if w.start < dur]
                lines = transcribe.group_words(words, self.settings)
                wav.unlink(missing_ok=True)
                self.cut_done.emit(key, lines)
                self.log.emit(f"✔ “{name}”: {len(words)} palavras, {len(lines)} legendas")
            self.progress.emit(100, "Transcrição concluída")
        except transcribe.Cancelled:
            self.log.emit("Transcrição cancelada.")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
