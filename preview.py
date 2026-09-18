#!/usr/bin/env python3
"""Локальный просмотр сайта из папки www/ — с поддержкой Range-запросов.

Встроенный `python -m http.server` Range не умеет, поэтому браузер качает видео
целиком (десятки мегабайт) и страница подвисает. Здесь этого нет — как на
настоящем хостинге.

    python preview.py                 # http://localhost:8765
    python preview.py 9000            # другой порт
    python preview.py 9000 C:\путь    # отдать другую папку
"""

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "www"


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        header = self.headers.get("Range")
        if not header or not header.startswith("bytes="):
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path) or not os.path.exists(path):
            return super().send_head()

        size = os.path.getsize(path)
        first, _, last = header[6:].partition("-")
        try:
            start = int(first) if first else 0
            end = int(last) if last else size - 1
        except ValueError:
            return super().send_head()

        end = min(end, size - 1)
        if start > end:
            self.send_error(416, "Requested Range Not Satisfiable")
            return None

        handle = open(path, "rb")
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return _Slice(handle, end - start + 1)

    def log_message(self, fmt, *args):
        # без шума от favicon; args могут быть не строками (например HTTPStatus),
        # поэтому приводим к тексту перед проверкой
        if "favicon" not in " ".join(str(a) for a in args):
            super().log_message(fmt, *args)


class _Slice:
    """Файл, отдающий только запрошенный диапазон байтов."""

    def __init__(self, handle, remaining):
        self.handle = handle
        self.remaining = remaining

    def read(self, amount=-1):
        if self.remaining <= 0:
            return b""
        if amount < 0 or amount > self.remaining:
            amount = self.remaining
        chunk = self.handle.read(amount)
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.handle.close()


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
ROOT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT
handler = partial(RangeHandler, directory=str(ROOT))
print(f"Сайт из {ROOT}")
print(f"Открой http://localhost:{port}/  (Ctrl+C — остановить)", flush=True)
try:
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
except KeyboardInterrupt:
    print("\nОстановлено.")
