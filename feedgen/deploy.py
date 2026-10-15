"""Загрузка docs/ на хостинг по FTP (или FTPS). Используется в GitHub Actions.

Настройки берутся из переменных окружения (секреты репозитория):
  FTP_SERVER, FTP_USERNAME, FTP_PASSWORD  – доступ
  FTP_PATH                                – папка на хостинге, куда кладутся файлы
  FTP_TLS                                 – "1" для FTPS (по умолчанию), "0" для обычного FTP
  FTP_PORT                                – порт, по умолчанию 21
  FTP_WORKERS                             – число параллельных соединений, по умолчанию 4

Правила:
  - XML, HTML и JSON загружаются всегда;
  - JPG-планировки имеют имя по хешу содержимого, поэтому грузятся только при
    отличии размера;
  - удаляются устаревшие файлы только внутри feeds/, preview/ и plans/.
    Остальное на хостинге (например, тестовый 1.html) не трогается.

Пароль никогда не печатается в лог.
"""

import ftplib
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger(__name__)

ALWAYS_UPLOAD = (".xml", ".html", ".json")
MANAGED_FOLDERS = ("feeds", "preview", "plans")


def _connect():
    host = os.environ["FTP_SERVER"]
    port = int(os.environ.get("FTP_PORT", "21"))
    use_tls = os.environ.get("FTP_TLS", "1") == "1"
    cls = ftplib.FTP_TLS if use_tls else ftplib.FTP
    ftp = cls()
    ftp.connect(host, port, timeout=60)
    ftp.login(os.environ["FTP_USERNAME"], os.environ["FTP_PASSWORD"])
    if use_tls:
        ftp.prot_p()
    ftp.set_pasv(True)
    ftp.voidcmd("TYPE I")
    return ftp


def _cd_or_make(ftp, rel_dir):
    """Переходит в подпапку, создавая ее при необходимости. Вызывается от корня FTP_PATH."""
    for part in [p for p in rel_dir.split("/") if p and p != "."]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def _walk_remote(ftp, folder):
    """Файлы и подпапки внутри папки на сервере (пути относительно корня FTP_PATH)."""
    files, dirs = [], []
    try:
        entries = list(ftp.mlsd(folder))
    except ftplib.error_perm:
        return files, dirs
    for name, facts in entries:
        if name in (".", ".."):
            continue
        path = f"{folder}/{name}"
        if facts.get("type") == "dir":
            sub_files, sub_dirs = _walk_remote(ftp, path)
            files += sub_files
            dirs += [path] + sub_dirs
        else:
            files.append(path)
    return files, dirs


def _upload_worker(jobs, base, counter, lock, total):
    """Один поток со своим FTP-соединением: загружает свою долю файлов."""
    ftp = _connect()
    done = skipped = 0
    try:
        for rel, path in jobs:
            ftp.cwd(base)
            _cd_or_make(ftp, os.path.dirname(rel))
            name = os.path.basename(rel)
            if not name.endswith(ALWAYS_UPLOAD):
                try:
                    if ftp.size(name) == path.stat().st_size:
                        skipped += 1
                        continue
                except ftplib.error_perm:
                    pass  # файла на сервере нет, загрузим
            with open(path, "rb") as f:
                ftp.storbinary(f"STOR {name}", f)
            done += 1
            with lock:
                counter[0] += 1
                if counter[0] % 200 == 0:
                    log.info("Загружено файлов: %d", counter[0])
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return done, skipped


def deploy(local_root, workers=None):
    local_root = Path(local_root)
    base = os.environ.get("FTP_PATH", "/").rstrip("/") or "/"
    workers = workers or int(os.environ.get("FTP_WORKERS", "4"))
    ftp = _connect()
    try:
        ftp.cwd(base)
    except ftplib.error_perm:
        # диагностика без секретов: что видно от корня входа
        root_names = ftp.nlst()[:50]
        log.error("Папка FTP_PATH не найдена. Видно от корня входа: %s", ", ".join(root_names) or "(пусто)")
        raise

    files = sorted((p.relative_to(local_root).as_posix(), p) for p in local_root.rglob("*") if p.is_file())
    wanted = {rel for rel, _ in files}

    # папки создаем заранее одним соединением, чтобы потоки не мешали друг другу
    for d in sorted({os.path.dirname(rel) for rel in wanted if os.path.dirname(rel)}):
        ftp.cwd(base)
        _cd_or_make(ftp, d)

    # раздаем файлы потокам по кругу, чтобы нагрузка была ровной
    shares = [files[i::workers] for i in range(workers)]
    counter, lock = [0], threading.Lock()
    uploaded = skipped = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for done, skip in ex.map(lambda j: _upload_worker(j, base, counter, lock, len(files)), shares):
            uploaded += done
            skipped += skip

    removed = 0
    for folder in MANAGED_FOLDERS:
        ftp.cwd(base)
        remote_files, dirs = _walk_remote(ftp, folder)
        for rel in remote_files:
            if rel not in wanted:
                ftp.cwd(base)
                ftp.delete(rel)
                removed += 1
        # пустые подпапки (от устаревших агентств или ЖК) тоже убираем: rmd сработает только на пустой папке
        for d in sorted(dirs, key=len, reverse=True):
            ftp.cwd(base)
            try:
                ftp.rmd(d)
            except ftplib.error_perm:
                pass

    ftp.quit()
    log.info("Загрузка на хостинг: загружено %d, без изменений %d, удалено устаревших %d (потоков %d)",
             uploaded, skipped, removed, workers)
    return {"uploaded": uploaded, "skipped": skipped, "removed": removed}
