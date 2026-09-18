#!/usr/bin/env python3
"""Синхронизация сайта по FTP/FTPS.

Настройки и пароль читаются из ftp.env рядом со скриптом.

Команды:
    python ftpsync.py diag          — проверить настройки и связь (пароль не показывается)
    python ftpsync.py test          — проверить подключение, показать список файлов
    python ftpsync.py pull          — скачать сайт с сервера в www/
    python ftpsync.py status        — что изменилось локально и что изменилось на сервере
    python ftpsync.py push          — залить изменённые файлы (с бэкапом старых версий)
    python ftpsync.py push путь ... — залить только указанные файлы
"""

from __future__ import annotations

import fnmatch
import ftplib
import hashlib
import json
import os
import posixpath
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

# в консоли Windows иначе ломается кириллица
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / "ftp.env"
LOCAL_ROOT = BASE / "www"
BACKUP_ROOT = BASE / "backups"
MANIFEST = BASE / ".ftpsync-manifest.json"

DEFAULT_EXCLUDES = [
    "*.log", "*.tmp", "*.swp", ".DS_Store", "Thumbs.db",
    "*/cache/*", "*/tmp/*", "*/logs/*", "*/.git/*", "*/node_modules/*",
]


# --------------------------------------------------------------------------- config

def load_config() -> dict:
    if not ENV_FILE.exists():
        sys.exit(
            f"Нет файла настроек: {ENV_FILE}\n"
            "Создай его по образцу ftp.env.example и впиши свои данные."
        )
    cfg = {}
    for raw in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip().upper()] = value.strip().strip('"').strip("'")

    missing = [k for k in ("FTP_HOST", "FTP_USER", "FTP_PASS") if not cfg.get(k)]
    if missing:
        sys.exit(f"В {ENV_FILE.name} не заполнено: {', '.join(missing)}")

    # частые опечатки в адресе: схема, слэш, пробелы
    raw_host = cfg["FTP_HOST"]
    host = raw_host.strip()
    for prefix in ("ftps://", "ftp://", "https://", "http://", "sftp://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix):]
    host = host.split("/")[0].strip().rstrip(".")
    if host != raw_host:
        print(f"FTP_HOST: использую '{host}' (в файле было '{raw_host}')")
    cfg["FTP_HOST"] = host

    cfg.setdefault("FTP_PORT", "21")
    cfg.setdefault("FTP_TLS", "1")
    cfg.setdefault("REMOTE_ROOT", "/")

    # путь: убрать пробелы и задвоенные слэши, оставить ведущий слэш
    raw_root = cfg["REMOTE_ROOT"]
    parts = [p for p in raw_root.replace("\\", "/").split("/") if p.strip()]
    root = "/" + "/".join(p.strip() for p in parts)
    if root != raw_root:
        print(f"REMOTE_ROOT: использую '{root}' (в файле было '{raw_root}')")
    cfg["REMOTE_ROOT"] = root
    cfg["EXCLUDE"] = [p.strip() for p in cfg.get("EXCLUDE", "").split(",") if p.strip()]
    cfg["MAX_SIZE_MB"] = float(cfg.get("MAX_SIZE_MB") or 5)
    return cfg


def excluded(relpath: str, extra: list[str]) -> bool:
    candidate = "/" + relpath
    for pattern in DEFAULT_EXCLUDES + extra:
        if fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(relpath, pattern):
            return True
    return False


# --------------------------------------------------------------------------- ftp

def connect(cfg: dict, attempts: int = 3) -> ftplib.FTP:
    """Подключиться, повторив попытку при обрыве связи.

    Хостинг иногда рвёт соединение на ровном месте (особенно если подряд идёт
    много сессий), поэтому одна неудача — ещё не повод падать.
    """
    for attempt in range(1, attempts + 1):
        try:
            return _connect_once(cfg)
        except ftplib.error_perm:
            raise  # неверный логин/пароль — повторять бессмысленно
        except ftplib.all_errors as exc:
            if attempt == attempts:
                raise
            reason = exc if str(exc) else type(exc).__name__
            print(f"Попытка {attempt} из {attempts} не удалась ({reason}), повтор через 3 с")
            time.sleep(3)
    raise RuntimeError("недостижимо")


def _connect_once(cfg: dict) -> ftplib.FTP:
    host, port = cfg["FTP_HOST"], int(cfg["FTP_PORT"])
    want_tls = cfg["FTP_TLS"] not in ("0", "no", "false", "")

    if want_tls:
        try:
            ftp = ftplib.FTP_TLS()
            ftp.connect(host, port, timeout=30)
            ftp.login(cfg["FTP_USER"], cfg["FTP_PASS"])
            ftp.prot_p()
            print("Подключение: FTPS (шифрованное)")
        except ftplib.all_errors as exc:
            # all_errors = Error + OSError + EOFError: сервер иногда просто
            # обрывает соединение на AUTH TLS, и это тоже повод откатиться
            reason = exc if str(exc) else type(exc).__name__
            print(f"FTPS не удался ({reason}); пробую обычный FTP")
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=30)
            ftp.login(cfg["FTP_USER"], cfg["FTP_PASS"])
            print("Подключение: FTP (без шифрования)")
    else:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=30)
        ftp.login(cfg["FTP_USER"], cfg["FTP_PASS"])
        print("Подключение: FTP (без шифрования)")

    ftp.set_pasv(True)
    ftp.encoding = "utf-8"
    return ftp


def list_dir(ftp: ftplib.FTP, path: str) -> list[tuple[str, str, int, str]]:
    """Вернуть [(имя, тип, размер, mtime)] для каталога. Тип: 'file' | 'dir'."""
    entries = []
    try:
        for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
            if name in (".", ".."):
                continue
            kind = facts.get("type", "")
            if kind == "dir":
                entries.append((name, "dir", 0, ""))
            elif kind == "file":
                entries.append((name, "file", int(facts.get("size", 0)),
                                facts.get("modify", "")))
        return entries
    except (ftplib.error_perm, ftplib.error_proto):
        pass  # сервер без MLSD — разбираем LIST

    lines: list[str] = []
    ftp.retrlines(f"LIST {path}", lines.append)
    for line in lines:
        parts = line.split(maxsplit=8)
        if len(parts) < 9 or not parts[0][0] in "d-l":
            continue
        name = parts[8]
        if name in (".", ".."):
            continue
        if parts[0].startswith("d"):
            entries.append((name, "dir", 0, ""))
        elif parts[0].startswith("-"):
            full = posixpath.join(path, name)
            try:
                mtime = ftp.sendcmd(f"MDTM {full}").split()[-1]
            except ftplib.all_errors:
                mtime = ""
            entries.append((name, "file", int(parts[4]), mtime))
    return entries


def walk_remote(ftp: ftplib.FTP, root: str, excludes: list[str],
                max_bytes: int = 0) -> tuple[dict[str, dict], dict[str, dict]]:
    """Рекурсивно собрать файлы сервера. Вернуть (обычные, пропущенные_по_размеру).

    Файлы крупнее max_bytes не качаются (0 = без ограничения): это медиа,
    которое мы всё равно не редактируем, а гонять его по FTP долго. Но знать
    о них надо — иначе они выглядят как «удалённые на сервере».
    """
    found: dict[str, dict] = {}
    skipped: dict[str, dict] = {}
    queue = [""]
    while queue:
        rel_dir = queue.pop()
        remote_dir = posixpath.join(root, rel_dir) if rel_dir else root
        try:
            entries = list_dir(ftp, remote_dir)
        except ftplib.all_errors as exc:
            print(f"  ! не читается {remote_dir}: {exc}")
            continue
        for name, kind, size, mtime in entries:
            rel = posixpath.join(rel_dir, name) if rel_dir else name
            if excluded(rel, excludes):
                continue
            if kind == "dir":
                queue.append(rel)
            elif max_bytes and size > max_bytes:
                skipped[rel] = {"size": size, "mtime": mtime}
            else:
                found[rel] = {"size": size, "mtime": mtime}

    if skipped:
        print(f"  не качаю, крупнее {max_bytes / 1048576:.0f} МБ "
              f"({len(skipped)} шт.):")
        for rel, info in sorted(skipped.items(), key=lambda kv: -kv[1]["size"]):
            print(f"    {info['size'] / 1048576:7.1f} МБ  {rel}")
    return found, skipped


def ensure_remote_dir(ftp: ftplib.FTP, root: str, rel_dir: str) -> None:
    current = root
    for part in rel_dir.split("/"):
        if not part:
            continue
        current = posixpath.join(current, part)
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass  # уже существует


# --------------------------------------------------------------------------- local

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_local(excludes: list[str]) -> dict[str, Path]:
    found = {}
    if not LOCAL_ROOT.exists():
        return found
    for path in LOCAL_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(LOCAL_ROOT).as_posix()
        if excluded(rel, excludes):
            continue
        found[rel] = path
    return found


def read_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def write_manifest(data: dict) -> None:
    MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- commands

def cmd_ls(cfg: dict, paths: list[str]) -> None:
    """Показать содержимое произвольных каталогов на сервере."""
    ftp = connect(cfg)
    try:
        print(f"Домашний каталог после входа: {ftp.pwd()}\n")
        for path in paths or [ftp.pwd()]:
            print(f"--- {path}")
            try:
                for name, kind, size, _ in sorted(list_dir(ftp, path)):
                    print(f"  {'<DIR>' if kind == 'dir' else f'{size:>10}'}  {name}")
            except ftplib.all_errors as exc:
                print(f"  ОШИБКА: {exc}")
            print()
    finally:
        ftp.quit()


def cmd_diag(cfg: dict) -> None:
    """Показать настройки (без пароля) и проверить связь по шагам."""
    host, port = cfg["FTP_HOST"], int(cfg["FTP_PORT"])
    password = cfg["FTP_PASS"]

    print("Настройки из ftp.env:")
    print(f"  FTP_HOST    = {host!r}")
    print(f"  FTP_PORT    = {port}")
    print(f"  FTP_USER    = {cfg['FTP_USER']!r}")
    print(f"  FTP_PASS    = задан, {len(password)} символов "
          f"(показывать не буду)")
    if password != password.strip():
        print("    !! в пароле есть пробелы по краям — возможно, лишние")
    print(f"  FTP_TLS     = {cfg['FTP_TLS']}")
    print(f"  REMOTE_ROOT = {cfg['REMOTE_ROOT']!r}")

    print("\nШаг 1. Разрешение имени в IP (DNS)")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        print(f"  OK: {host} -> {', '.join(addrs)}")
    except socket.gaierror as exc:
        print(f"  ОШИБКА: имя '{host}' не разрешается ({exc.strerror or exc})")
        print("  Причины: опечатка в адресе, или домен ещё не делегирован.")
        print("  Попробуй вписать в FTP_HOST прямой IP-адрес сервера.")
        return

    print(f"\nШаг 2. TCP-соединение с портом {port}")
    try:
        with socket.create_connection((host, port), timeout=15) as sock:
            banner = sock.recv(256).decode("utf-8", "replace").strip()
        print(f"  OK, сервер ответил: {banner.splitlines()[0] if banner else '(без баннера)'}")
    except OSError as exc:
        print(f"  ОШИБКА: порт недоступен ({exc})")
        print("  Причины: FTP выключен, другой порт, или блокирует файрвол/провайдер.")
        return

    print("\nШаг 3. Вход по логину и паролю")
    try:
        ftp = connect(cfg)
    except ftplib.error_perm as exc:
        print(f"  ОШИБКА входа: {exc}")
        print("  Логин или пароль не подходят — проверь их в ISPmanager.")
        return
    try:
        print(f"  OK, вошли под {cfg['FTP_USER']}")
        print(f"\nШаг 4. Каталог {cfg['REMOTE_ROOT']!r}")
        entries = list_dir(ftp, cfg["REMOTE_ROOT"])
        print(f"  OK, объектов внутри: {len(entries)}")
        for name, kind, size, _ in sorted(entries)[:20]:
            print(f"    {'<DIR>' if kind == 'dir' else f'{size:>10}'}  {name}")
    finally:
        ftp.quit()


def cmd_test(cfg: dict) -> None:
    ftp = connect(cfg)
    try:
        root = cfg["REMOTE_ROOT"]
        print(f"Каталог на сервере: {root}\n")
        for name, kind, size, mtime in sorted(list_dir(ftp, root)):
            marker = "<DIR>" if kind == "dir" else f"{size:>10}"
            print(f"  {marker}  {name}")
    finally:
        ftp.quit()


def cmd_pull(cfg: dict, force: bool = False) -> None:
    ftp = connect(cfg)
    root = cfg["REMOTE_ROOT"]
    manifest = read_manifest()
    try:
        print(f"Читаю дерево файлов на сервере ({root}) ...")
        remote, skipped = walk_remote(ftp, root, cfg["EXCLUDE"],
                                      int(cfg["MAX_SIZE_MB"] * 1048576))
        print(f"Найдено файлов: {len(remote)}\n")

        # крупные файлы не качаем, но их прошлые записи сохраняем —
        # иначе они выглядели бы как новые и заливались бы обратно
        new_manifest = {rel: manifest[rel] for rel in skipped if rel in manifest}
        downloaded = skipped = 0
        for rel in sorted(remote):
            local_path = LOCAL_ROOT / rel
            record = manifest.get(rel)

            if not force and local_path.exists() and record:
                if sha256(local_path) != record.get("local_sha"):
                    print(f"  ПРОПУСК (локально изменён): {rel}")
                    new_manifest[rel] = record
                    skipped += 1
                    continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            with local_path.open("wb") as handle:
                ftp.retrbinary(f"RETR {posixpath.join(root, rel)}", handle.write)
            new_manifest[rel] = {
                "remote_size": remote[rel]["size"],
                "remote_mtime": remote[rel]["mtime"],
                "local_sha": sha256(local_path),
            }
            downloaded += 1
            print(f"  скачан: {rel}")

        write_manifest(new_manifest)
        print(f"\nГотово. Скачано: {downloaded}, пропущено: {skipped}")
        print(f"Локальная копия: {LOCAL_ROOT}")
    finally:
        ftp.quit()


def classify(cfg: dict, ftp: ftplib.FTP) -> tuple[dict, list, list, list, list]:
    root = cfg["REMOTE_ROOT"]
    manifest = read_manifest()
    remote, _ = walk_remote(ftp, root, cfg["EXCLUDE"],
                            int(cfg["MAX_SIZE_MB"] * 1048576))
    local = walk_local(cfg["EXCLUDE"])

    local_new, local_mod, local_del, remote_mod = [], [], [], []

    for rel, path in sorted(local.items()):
        record = manifest.get(rel)
        if record is None:
            local_new.append(rel)
        elif sha256(path) != record.get("local_sha"):
            local_mod.append(rel)

    for rel in sorted(manifest):
        if rel not in local:
            local_del.append(rel)

    for rel, info in sorted(remote.items()):
        record = manifest.get(rel)
        if record and (info["size"] != record.get("remote_size")
                       or info["mtime"] != record.get("remote_mtime")):
            remote_mod.append(rel)

    return manifest, local_new, local_mod, local_del, remote_mod


def cmd_status(cfg: dict) -> None:
    ftp = connect(cfg)
    try:
        _, new, mod, deleted, remote_mod = classify(cfg, ftp)
        def show(title, items):
            print(f"\n{title} ({len(items)}):")
            for rel in items:
                print(f"  {rel}")
            if not items:
                print("  —")
        show("Новые локальные файлы", new)
        show("Изменены локально", mod)
        show("Удалены локально (на сервере остаются)", deleted)
        show("!! Изменены на сервере помимо нас", remote_mod)
    finally:
        ftp.quit()


def cmd_push(cfg: dict, only: list[str], assume_yes: bool = False) -> None:
    ftp = connect(cfg)
    root = cfg["REMOTE_ROOT"]
    try:
        manifest, new, mod, _, remote_mod = classify(cfg, ftp)
        targets = sorted(set(new) | set(mod))

        if only:
            wanted = {p.replace("\\", "/").lstrip("./") for p in only}
            targets = [t for t in targets if t in wanted]
            unknown = wanted - set(targets)
            if unknown:
                print(f"Не найдены среди изменённых: {', '.join(sorted(unknown))}")

        if not targets:
            print("Заливать нечего — изменений нет.")
            return

        conflicts = [t for t in targets if t in remote_mod]
        if conflicts:
            print("ВНИМАНИЕ: эти файлы менялись и на сервере, их перезапишем:")
            for rel in conflicts:
                print(f"  {rel}")

        print(f"\nБудет залито файлов: {len(targets)}")
        for rel in targets:
            print(f"  {rel}")
        if not assume_yes:
            if input("\nЗаливаем? (yes/no): ").strip().lower() not in ("y", "yes", "да"):
                print("Отменено.")
                return

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = BACKUP_ROOT / stamp
        uploaded = 0
        failed = []

        for rel in targets:
            # хостинг любит рвать длинные сессии, поэтому каждый файл —
            # отдельная попытка с переподключением, а манифест пишется сразу,
            # чтобы повторный запуск продолжил с места обрыва
            for attempt in range(1, 4):
                try:
                    _upload_one(ftp, root, rel, backup_dir, manifest)
                    write_manifest(manifest)
                    uploaded += 1
                    print(f"  ЗАЛИТ: {rel}")
                    break
                except ftplib.error_perm as exc:
                    print(f"  ОТКАЗ: {rel} — {exc}")
                    failed.append(rel)
                    break
                except ftplib.all_errors as exc:
                    reason = exc if str(exc) else type(exc).__name__
                    if attempt == 3:
                        print(f"  НЕ УДАЛОСЬ: {rel} — {reason}")
                        failed.append(rel)
                        break
                    print(f"  обрыв на {rel} ({reason}); переподключаюсь")
                    try:
                        ftp.close()
                    except Exception:
                        pass
                    time.sleep(5)
                    ftp = connect(cfg)

        write_manifest(manifest)
        print(f"\nГотово. Залито файлов: {uploaded} из {len(targets)}")
        if backup_dir.exists():
            print(f"Бэкап прежних версий: {backup_dir}")
        if failed:
            print(f"\nНе залились ({len(failed)}) — запустите push ещё раз:")
            for rel in failed:
                print(f"  {rel}")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def _upload_one(ftp: ftplib.FTP, root: str, rel: str,
                backup_dir: Path, manifest: dict) -> None:
    """Сохранить прежнюю версию файла и залить новую."""
    remote_path = posixpath.join(root, rel)

    backup_path = backup_dir / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with backup_path.open("wb") as handle:
            ftp.retrbinary(f"RETR {remote_path}", handle.write)
        print(f"  бэкап: {rel}")
    except ftplib.error_perm:
        backup_path.unlink(missing_ok=True)  # файла на сервере не было — он новый

    rel_dir = posixpath.dirname(rel)
    if rel_dir:
        ensure_remote_dir(ftp, root, rel_dir)

    local_path = LOCAL_ROOT / rel
    with local_path.open("rb") as handle:
        ftp.storbinary(f"STOR {remote_path}", handle)

    try:
        mtime = ftp.sendcmd(f"MDTM {remote_path}").split()[-1]
    except ftplib.all_errors:
        mtime = ""
    manifest[rel] = {
        "remote_size": local_path.stat().st_size,
        "remote_mtime": mtime,
        "local_sha": sha256(local_path),
    }


# --------------------------------------------------------------------------- main

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    command, rest = args[0], args[1:]
    cfg = load_config()

    if command == "diag":
        cmd_diag(cfg)
    elif command == "ls":
        cmd_ls(cfg, rest)
    elif command == "test":
        cmd_test(cfg)
    elif command == "pull":
        cmd_pull(cfg, force="--force" in rest)
    elif command == "status":
        cmd_status(cfg)
    elif command == "push":
        cmd_push(cfg, [a for a in rest if not a.startswith("--")],
                 assume_yes="--yes" in rest)
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except ftplib.error_perm as exc:
        sys.exit(f"Сервер отклонил запрос: {exc}\n"
                 "Похоже на неверный логин/пароль или нехватку прав.")
    except EOFError:
        sys.exit(
            "Сервер оборвал соединение сразу после приветствия.\n"
            "Обычно это временная блокировка по IP за частые подключения:\n"
            "  — подождите 15-30 минут и повторите;\n"
            "  — либо снимите блокировку в ISPmanager: Настройки → Брандмауэр\n"
            "    (или раздел с заблокированными адресами)."
        )
    except ftplib.all_errors as exc:
        sys.exit(f"Ошибка связи с сервером: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nПрервано.")
