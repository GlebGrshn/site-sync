#!/usr/bin/env python3
"""Пережимает видео для сайта: 4K с телефона -> 1080p H.264.

ffmpeg берётся из пакета imageio-ffmpeg, отдельная установка не нужна:
    pip install imageio-ffmpeg

    python compress.py
"""

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

DESKTOP = Path.home() / "Desktop"
BOXES = DESKTOP / "шкатулки"
WWW = Path(__file__).resolve().parent / "www"
PAMYAT = DESKTOP / "pamyat"

# (исходник, результат, оставлять ли звук)
JOBS = [
    # ролики шкатулок: в каталоге они идут без звука, поэтому дорожку вырезаем
    (BOXES / "IMG_0193.mp4", WWW / "voennaya-video.mp4", False),
    (BOXES / "IMG_0215.mp4", WWW / "klassika-video.mp4", False),
    (BOXES / "IMG_0199.mp4", WWW / "knizhka-video.mp4", False),
    (BOXES / "IMG_0219.mp4", WWW / "vydvizhnaya-video.mp4", False),
    # видео на сайте памяти: звук там нужен
    (PAMYAT / "site-5-war" / "media" / "video-1.mp4",
     WWW / "pamyat" / "site-5-war" / "media" / "video-1.mp4", True),
]


def megabytes(path: Path) -> float:
    return path.stat().st_size / 1048576


def compress(source: Path, target: Path, keep_audio: bool) -> None:
    if not source.exists():
        print(f"  нет исходника: {source}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)

    command = [
        FFMPEG, "-y", "-i", str(source),
        # уменьшаем до 1080p по высоте, ширину считаем сами и округляем до чётной
        "-vf", "scale=-2:'min(1080,ih)'",
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        # индекс в начало файла: браузер начинает играть, не скачав всё
        "-movflags", "+faststart",
    ]
    command += ["-an"] if not keep_audio else ["-c:a", "aac", "-b:a", "128k"]
    command += [str(target)]

    before = megabytes(source)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        print(f"  ОШИБКА {source.name}: " + " | ".join(tail))
        return

    after = megabytes(target)
    звук = "со звуком" if keep_audio else "без звука"
    print(f"  {target.name:<26} {before:6.1f} МБ -> {after:5.1f} МБ  ({звук})")


print(f"ffmpeg: {FFMPEG}\n")
for source, target, keep_audio in JOBS:
    compress(source, target, keep_audio)
print("\nГотово.")
