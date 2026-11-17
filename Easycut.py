import csv
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
ffmpeg = root / "ffmpeg" / "bin" / "ffmpeg.exe"

cam1 = root / "input" / "cam1.mp4"
cam2 = root / "input" / "cam2.mp4"
cortes_csv = root / "config" / "cortes.csv"
legendas_csv = root / "config" / "legendas.csv"
temp_dir = root / "temp"
output_dir = root / "output"
assets_dir = root / "assets"
logo_path = assets_dir / "logo.png"

logo_width = 250
logo_opacity = 0.88
logo_x = 40
logo_y = 80

temp_dir.mkdir(parents=True, exist_ok=True)
output_dir.mkdir(parents=True, exist_ok=True)

if not ffmpeg.exists():
    sys.exit(f"FFmpeg não encontrado: {ffmpeg}")
if not cam1.exists():
    sys.exit(f"Cam 1 não encontrada: {cam1}")
if not cam2.exists():
    sys.exit(f"Cam 2 não encontrada: {cam2}")
if not cortes_csv.exists():
    sys.exit(f"cortes.csv não encontrado: {cortes_csv}")
if not legendas_csv.exists():
    sys.exit(f"legendas.csv não encontrado: {legendas_csv}")


def to_seconds(value):
    text = str(value).strip()
    if re.fullmatch(r"\d+([.,]\d+)?", text):
        return float(text.replace(",", "."))
    # Mesmo formato aceito pelo TimeSpan.Parse: [d.]h:m[:s[.fração]]
    m = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+)(?::(\d+)(?:[.,](\d+))?)?", text)
    if not m:
        raise ValueError(f"Tempo inválido: {text!r}")
    d, h, mi, s, frac = m.groups()
    seconds = int(d or 0) * 86400 + int(h) * 3600 + int(mi) * 60 + int(s or 0)
    if frac:
        seconds += float("0." + frac)
    return seconds


def format_ass_time(seconds):
    ms = round(max(0.0, float(seconds)) * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h}:{m:02}:{s:02}.{ms // 10:02}"


def escape_ass_text(text):
    t = str(text)
    t = t.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    t = t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return t.strip()


def to_ffmpeg_path(path):
    return os.path.abspath(path).replace("\\", "/").replace(":", "\\:")


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def to_karaoke_text(text, start, end):
    words = escape_ass_text(text).split()
    if not words:
        return ""

    duration_cs = max(1, round((end - start) * 100))
    per_word = max(1, duration_cs // len(words))
    remaining = duration_cs

    out = []
    for i, word in enumerate(words):
        if i == len(words) - 1:
            k = max(1, remaining)
        else:
            k = per_word
            remaining -= k
        out.append(f"{{\\kf{k}}}{word}")

    return " ".join(out)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


cortes = read_csv(cortes_csv)
legendas = read_csv(legendas_csv)

print()
print("FÁBRICA DE CORTES — MODO SIMPLES")
print(f"Cortes encontrados: {len(cortes)}")
print()

ASS_HEADER = """[Script Info]
Title: {id}
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Bazinga!,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,1,0,0,100,100,0,0,1,4,1,5,80,80,0,1
Style: Purple,Bazinga!,72,&H00FF358A,&HFFFFFFFF,&HFF000000,&HFF000000,1,1,0,0,100,100,0,0,1,0,0,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".replace("\n", "\r\n")

for cut in cortes:
    cut_id = cut["id"].strip()
    safe_id = safe_name(cut_id)

    start = to_seconds(cut["start"])
    end = to_seconds(cut["end"])
    duration = end - start

    if duration <= 0:
        print(f"Pulando corte inválido: {cut_id}")
        continue

    print(f"Renderizando: {cut_id}")

    ass_path = temp_dir / f"{safe_id}.ass"
    ass_filter_path = to_ffmpeg_path(ass_path)

    ass = ASS_HEADER.format(id=cut_id)

    # -eq do PowerShell compara sem diferenciar maiúsculas/minúsculas
    linhas = [l for l in legendas if l["id"].strip().casefold() == cut_id.casefold()]

    for line in linhas:
        ls = to_seconds(line["start"])
        le = to_seconds(line["end"])
        txt = escape_ass_text(line["text"])

        if ls < 0:
            ls = 0
        if le > duration:
            le = duration

        if le > ls and txt != "":
            karaoke_text = to_karaoke_text(line["text"], ls, le)

            # Camada branca base com borda preta
            ass += f"Dialogue: 0,{format_ass_time(ls)},{format_ass_time(le)},Base,,0,0,0,,{txt}\r\n"

            # Camada roxa progressiva por cima
            ass += f"Dialogue: 1,{format_ass_time(ls)},{format_ass_time(le)},Purple,,0,0,0,,{karaoke_text}\r\n"

    with open(ass_path, "w", encoding="utf-8", newline="") as f:
        f.write(ass)

    output_path = output_dir / f"{safe_id}.mp4"

    ass_filter = f"ass=filename='{ass_filter_path}'"
    stack = (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setpts=PTS-STARTPTS[top];"
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setpts=PTS-STARTPTS[bottom];"
        f"[top][bottom]vstack=inputs=2,{ass_filter}"
    )

    if logo_path.exists():
        logo_input = ["-loop", "1", "-i", str(logo_path)]
        filter_complex = (
            f"{stack}[base];"
            f"[2:v]scale={logo_width}:-1,format=rgba,colorchannelmixer=aa={logo_opacity}[logo];"
            f"[base][logo]overlay={logo_x}:{logo_y}[v]"
        )
    else:
        logo_input = []
        filter_complex = f"{stack}[v]"

    cmd = [
        str(ffmpeg), "-y",
        "-ss", str(start), "-t", str(duration), "-i", str(cam1),
        "-ss", str(start), "-t", str(duration), "-i", str(cam2),
        *logo_input,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path),
    ]

    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"Erro ao renderizar: {cut_id}")

    print(f"Finalizado: {output_path}")

print()
print("Todos os cortes foram finalizados.")
print(f"Arquivos em: {output_dir}")
