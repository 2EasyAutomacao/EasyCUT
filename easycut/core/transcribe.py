"""Integração opcional com Whisper local.

Back-ends suportados (todos opcionais, detectados em tempo de execução):
  * faster-whisper  — `pip install faster-whisper` (Linux/macOS, CPU ou CUDA)
  * openai-whisper  — `pip install openai-whisper`
  * mlx-whisper     — `pip install mlx-whisper` (macOS Apple Silicon)
  * whisper.cpp     — executável `whisper-cli` + modelo ggml .bin (brew install whisper-cpp)
"""
from __future__ import annotations

import importlib.util
import json
import platform
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .models import SubtitleLine, WhisperSettings, Word
from .tools import find_whisper_cpp

BACKENDS = [
    ("faster-whisper", "faster-whisper"),
    ("openai-whisper", "openai-whisper"),
    ("mlx-whisper", "mlx-whisper (Apple Silicon)"),
    ("whisper.cpp", "whisper.cpp (whisper-cli)"),
]

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]

_MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}

Log = Callable[[str], None]


class TranscriptionError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def backend_status(s: WhisperSettings) -> dict[str, str | None]:
    """None = disponível; texto = o que falta."""
    st: dict[str, str | None] = {}
    st["faster-whisper"] = None if _has_module("faster_whisper") else "pip install faster-whisper"
    st["openai-whisper"] = None if _has_module("whisper") else "pip install openai-whisper"
    if not is_apple_silicon():
        st["mlx-whisper"] = "apenas macOS com Apple Silicon"
    else:
        st["mlx-whisper"] = None if _has_module("mlx_whisper") else "pip install mlx-whisper"
    if not find_whisper_cpp(s.cpp_bin):
        st["whisper.cpp"] = "whisper-cli não encontrado (brew install whisper-cpp)"
    elif not s.cpp_model or not Path(s.cpp_model).is_file():
        st["whisper.cpp"] = "escolha o arquivo de modelo ggml (.bin)"
    else:
        st["whisper.cpp"] = None
    return st


def resolve_backend(s: WhisperSettings) -> str:
    status = backend_status(s)
    if s.backend != "auto":
        if status.get(s.backend) is not None:
            raise TranscriptionError(f"{s.backend} indisponível: {status[s.backend]}")
        return s.backend
    order = (["mlx-whisper", "whisper.cpp", "faster-whisper", "openai-whisper"] if is_apple_silicon()
             else ["faster-whisper", "whisper.cpp", "openai-whisper"])
    for b in order:
        if status.get(b) is None:
            return b
    raise TranscriptionError(
        "Nenhum Whisper local encontrado. Instale um deles, por exemplo:\n"
        "  pip install faster-whisper\n"
        "ou, no Mac com Apple Silicon:  pip install mlx-whisper"
    )


def _language(s: WhisperSettings) -> str | None:
    lang = (s.language or "").strip().lower()
    return None if lang in ("", "auto") else lang


_cache: dict[tuple, object] = {}


def _load_wav(path: Path):
    """WAV PCM 16-bit mono 16 kHz (gerado pelo ffmpeg do app) → float32 numpy.

    Passar o áudio já decodificado evita que os back-ends Python dependam do
    PyAV ou de um `ffmpeg` no PATH (que falta em apps abertos pelo Finder no macOS).
    """
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _cached(key: tuple, factory):
    if key not in _cache:
        _cache.clear()  # mantém só um modelo em memória
        _cache[key] = factory()
    return _cache[key]


def _faster(path: Path, s: WhisperSettings, log: Log, cancel: threading.Event) -> list[Word]:
    from faster_whisper import WhisperModel

    def load():
        device = s.device
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = "float16" if device == "cuda" else "int8"
        log(f"Carregando modelo faster-whisper '{s.model}' ({device}, {compute}) — "
            "na primeira vez ele é baixado…")
        return WhisperModel(s.model, device=device, compute_type=compute)

    model = _cached(("faster", s.model, s.device), load)
    segments, _info = model.transcribe(_load_wav(path), language=_language(s),
                                       word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        if cancel.is_set():
            raise Cancelled
        for w in seg.words or []:
            words.append(Word(float(w.start), float(w.end), w.word.strip()))
    return words


def _from_whisper_result(result: dict) -> list[Word]:
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []) or []:
            words.append(Word(float(w["start"]), float(w["end"]), str(w["word"]).strip()))
    return words


def _openai(path: Path, s: WhisperSettings, log: Log, cancel: threading.Event) -> list[Word]:
    import whisper
    if not hasattr(whisper, "load_model"):
        raise TranscriptionError("O pacote 'whisper' instalado não é o openai-whisper.")

    def load():
        log(f"Carregando modelo openai-whisper '{s.model}' — na primeira vez ele é baixado…")
        return whisper.load_model(s.model, device=None if s.device == "auto" else s.device)

    model = _cached(("openai", s.model, s.device), load)
    fp16 = getattr(getattr(model, "device", None), "type", "cpu") == "cuda"
    result = model.transcribe(_load_wav(path), language=_language(s), word_timestamps=True,
                              verbose=None, fp16=fp16)
    return _from_whisper_result(result)


def _mlx(path: Path, s: WhisperSettings, log: Log, cancel: threading.Event) -> list[Word]:
    import mlx_whisper
    repo = s.model if ("/" in s.model or Path(s.model).exists()) else _MLX_REPOS.get(
        s.model, f"mlx-community/whisper-{s.model}")
    log(f"mlx-whisper: {repo}")
    result = mlx_whisper.transcribe(_load_wav(path), path_or_hf_repo=repo, language=_language(s),
                                    word_timestamps=True, verbose=None)
    return _from_whisper_result(result)


def _whisper_cpp(path: Path, s: WhisperSettings, log: Log, cancel: threading.Event) -> list[Word]:
    exe = find_whisper_cpp(s.cpp_bin)
    out_base = path.with_suffix("")
    out_json = Path(str(out_base) + ".json")
    out_json.unlink(missing_ok=True)
    # -ml 1 -sow: um segmento por palavra → tempos por palavra
    cmd = [exe, "-m", s.cpp_model, "-f", str(path), "-l", _language(s) or "auto",
           "-oj", "-ml", "1", "-sow", "-of", str(out_base), "-np"]
    log("whisper.cpp: " + " ".join(cmd))
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
        while proc.poll() is None:
            if cancel.wait(0.2):
                proc.kill()
                proc.wait()
                raise Cancelled
        if proc.returncode != 0:
            err.seek(0)
            raise TranscriptionError(err.read().decode(errors="replace")[-2000:])
    data = json.loads(out_json.read_text(encoding="utf-8", errors="replace"))
    words = []
    for seg in data.get("transcription", []):
        text = str(seg.get("text", "")).strip()
        if not text or re.fullmatch(r"\[.*\]|\(.*\)", text):
            continue
        off = seg.get("offsets", {})
        words.append(Word(off.get("from", 0) / 1000, off.get("to", 0) / 1000, text))
    return words


_RUNNERS = {
    "faster-whisper": _faster,
    "openai-whisper": _openai,
    "mlx-whisper": _mlx,
    "whisper.cpp": _whisper_cpp,
}


def transcribe_file(path: Path, s: WhisperSettings, backend: str, log: Log,
                    cancel: threading.Event) -> list[Word]:
    words = _RUNNERS[backend](path, s, log, cancel)
    if cancel.is_set():
        raise Cancelled
    return [w for w in words if w.text]


_PUNCT = re.compile(r"[^\w\s'’\-]", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?…]$")


def group_words(words: list[Word], s: WhisperSettings) -> list[SubtitleLine]:
    """Agrupa palavras em linhas curtas no estilo Reels/Shorts."""
    lines: list[SubtitleLine] = []
    cur: list[Word] = []
    ends_sentence = False

    def flush():
        if cur:
            lines.append(SubtitleLine(cur[0].start, cur[-1].end,
                                      " ".join(w.text for w in cur), list(cur)))
            cur.clear()

    for w in words:
        raw = w.text.strip()
        text = _PUNCT.sub("", raw).strip() if s.remove_punct else raw
        if not text:
            continue
        w = Word(w.start, max(w.end, w.start + 0.01), text)
        if cur:
            chars = len(" ".join(x.text for x in cur)) + 1 + len(text)
            if (len(cur) >= s.max_words or chars > s.max_chars or ends_sentence
                    or w.start - cur[-1].end > s.max_gap
                    or w.end - cur[0].start > s.max_duration):
                flush()
        cur.append(w)
        ends_sentence = bool(_SENTENCE_END.search(raw))
    flush()

    # Fecha buracos curtos para a legenda não piscar entre linhas.
    for a, b in zip(lines, lines[1:]):
        if 0 < b.start - a.end < 0.4:
            a.end = b.start
    for ln in lines:
        ln.end = max(ln.end, ln.start + 0.3)
    return lines
