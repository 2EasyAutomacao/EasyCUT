"""Montagem dos comandos do ffmpeg (render, quadro de prévia, áudio)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .models import Cut, LayoutSettings, Project, even

Region = tuple[int, int, int, int, int]  # (câmera, x, y, w, h)


def effective_mode(layout: LayoutSettings, has_cam2: bool) -> str:
    if layout.mode in ("stack", "side") and not has_cam2:
        return "single"
    return layout.mode if layout.mode in ("stack", "side", "single") else "stack"


def layout_regions(layout: LayoutSettings, has_cam2: bool) -> list[Region]:
    """Retângulos de cada câmera no quadro final (compartilhado com a prévia)."""
    W, H = layout.size()
    mode = effective_mode(layout, has_cam2)
    first, second = (2, 1) if layout.swap and has_cam2 else (1, 2)
    split = min(0.9, max(0.1, float(layout.split)))
    if mode == "single":
        return [(first, 0, 0, W, H)]
    if mode == "stack":
        h1 = min(H - 2, even(H * split))
        return [(first, 0, 0, W, h1), (second, 0, h1, W, H - h1)]
    w1 = min(W - 2, even(W * split))
    return [(first, 0, 0, w1, H), (second, w1, 0, W - w1, H)]


def audio_camera(project: Project) -> int:
    return 2 if project.layout.audio_source == 2 and project.cam2 else 1


def cam_path(project: Project, cam: int) -> str:
    return project.cam2 if cam == 2 else project.cam1


def _filter_path(path: Path | str) -> str:
    s = str(Path(path).resolve()).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def build_render_command(
    ffmpeg: str,
    project: Project,
    cut: Cut,
    ass_path: Path | None,
    fonts_dir: Path | None,
    output: Path,
    *,
    preview_at: float | None = None,
) -> list[str]:
    """Comando completo. Com `preview_at` (segundos dentro do corte) gera um único PNG."""
    lay = project.layout
    W, H = lay.size()
    regions = layout_regions(lay, bool(project.cam2))
    cams = {r[0] for r in regions}
    if preview_at is None:
        cams.add(audio_camera(project))
    order = sorted(cams)
    idx = {cam: i for i, cam in enumerate(order)}
    start = cut.start + (preview_at or 0.0)

    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    for cam in order:
        args += ["-ss", f"{start:.3f}"]
        if preview_at is None:
            args += ["-t", f"{cut.duration:.3f}"]
        args += ["-i", cam_path(project, cam)]

    logo = project.logo
    use_logo = logo.enabled and logo.path and Path(logo.path).is_file()
    if use_logo:
        args += ["-i", logo.path]
        logo_idx = len(order)

    parts = []
    for k, (cam, _x, y, w, h) in enumerate(regions):
        parts.append(
            f"[{idx[cam]}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,setpts=PTS-STARTPTS[r{k}]"
        )
    if len(regions) == 2:
        stacker = "vstack" if regions[1][2] > 0 else "hstack"
        parts.append(f"[r0][r1]{stacker}=inputs=2[base]")
        cur = "base"
    else:
        cur = "r0"

    if use_logo:
        opacity = min(1.0, max(0.0, logo.opacity))
        parts.append(f"[{logo_idx}:v]scale={max(2, int(logo.width))}:-1,format=rgba,"
                     f"colorchannelmixer=aa={opacity:.3f}[logo]")
        parts.append(f"[{cur}][logo]overlay={int(logo.x)}:{int(logo.y)}[withlogo]")
        cur = "withlogo"

    chain = []
    if preview_at:
        chain.append(f"setpts=PTS+{preview_at:.3f}/TB")  # legendas no tempo certo
    if ass_path:
        ass = f"ass=filename='{_filter_path(ass_path)}'"
        if fonts_dir:
            ass += f":fontsdir='{_filter_path(fonts_dir)}'"
        chain.append(ass)
    chain.append("format=yuv420p" if preview_at is None else "format=rgb24")
    parts.append(f"[{cur}]{','.join(chain)}[v]")

    args += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if preview_at is not None:
        return args + ["-frames:v", "1", "-update", "1", str(output)]

    r = project.render
    args += [
        "-map", f"{idx[audio_camera(project)]}:a?",
        "-c:v", "libx264", "-preset", r.preset, "-crf", str(int(r.crf)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", r.audio_bitrate,
        "-movflags", "+faststart",
        "-t", f"{cut.duration:.3f}",
        "-progress", "pipe:1", "-nostats",
        str(output),
    ]
    return args


def grab_frame(ffmpeg: str, path: str, t: float, max_width: int = 1280) -> bytes:
    """Um quadro do vídeo original em PNG (para a prévia interativa)."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
        "-vf", f"scale='min({max_width},iw)':-2", "-f", "image2pipe", "-c:v", "png", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode(errors="replace").strip() or "falha ao ler quadro")
    return res.stdout


def probe_duration(ffprobe: str | None, path: str) -> float | None:
    if not ffprobe:
        return None
    try:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(res.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def extract_audio(ffmpeg: str, src: str, start: float, duration: float, out_wav: Path) -> None:
    """WAV mono 16 kHz do trecho do corte (entrada do Whisper)."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg não conseguiu extrair o áudio: {res.stderr.strip()}")
