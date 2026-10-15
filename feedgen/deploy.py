"""Загрузка docs/ на хостинг по FTP (или FTPS). Используется в GitHub Actions.

Настройки берутся из переменных окружения (секреты репозитория):
  FTP_SERVER, FTP_USERNAME, FTP_PASSWORD  – доступ
  FTP_PATH                                – папка на хостинге, куда кладутся файлы
  FTP_TLS                                 – "1" для FTPS (по умолчанию), "0" для обычного FTP
  FTP_PORT                                – порт, по умолчанию 21
  FTP_WORKERS                             – число параллельных соединений, по умолчанию 4

Правила:
  - XML, HTML и JSON загружаются всегда;
  - JPG-планировки и логотипы грузятся только при отличии размера
    (имя планировки зависит от содержимого);
  - удаляются устаревшие файлы только внутри feeds/, preview/ и plans/.
    Остальное на хостинге (например, тестовый 1.html) не трогается.

Надежность:
  - соединения долго не держим: сервер закрывает простаивающие;
  - при обрыве поток переподключается и повторяет файл (до 4 попыток);
  - ошибка по отдельному файлу не роняет всю загрузку сразу, но итоговый код ошибки
    возвращается, если файл так и не загрузился.

Пароль никогда не печатается в лог.
"""

import ftplib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger(__name__)

ALWAYS_UPLOAD = (".xml", ".html", ".json")
MANAGED_FOLDERS = ("feeds", "preview", "plans")
ATTEMPTS = 4
NETWORK_ERRORS = (EOFError, OSError, ftplib.error_temp, ftplib.error_reply, ftplib.error_proto)


def _connect(retries=4):
    host = os.environ["FTP_SERVER"]
    port = int(os.environ.get("FTP_PORT", "21"))
    use_tls = os.environ.get("FTP_TLS", "1") == "1"
    cls = ftplib.FTP_TLS if use_tls else ftplib.FTP
    last = None
    for attempt in range(retries):
        try:
            ftp = cls()
            ftp.connect(host, port, timeout=60)
            ftp.login(os.environ["FTP_USERNAME"], os.environ["FTP_PASSWORD"])
            if use_tls:
                ftp.prot_p()
            ftp.set_pasv(True)
            ftp.voidcmd("TYPE I")
            return ftp
        except ftplib.error_perm:
            raise                                   # неверный логин или пароль: повторять бесполезно
        except NETWORK_ERRORS as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Не удалось подключиться к FTP: {type(last).__name__}")


def _quit(ftp):
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


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


def _upload_one(ftp, base, rel, path):
    """Загружает один файл. Возвращает 'uploaded' или 'skipped'."""
    ftp.cwd(base)
    _cd_or_make(ftp, os.path.dirname(rel))
    name = os.path.basename(rel)
    if not name.endswith(ALWAYS_UPLOAD):
        try:
            if ftp.size(name) == path.stat().st_size:
                return "skipped"
        except ftplib.error_perm:
            pass  # файла на сервере нет, загрузим
    with open(path, "rb") as f:
        ftp.storbinary(f"STOR {name}", f)
    return "uploaded"


def _upload_worker(jobs, base, counter, lock):
    """Один поток со своим соединением: загружает свою долю файлов, при обрыве переподключается."""
    ftp = _connect()
    done = skipped = 0
    failed = []
    try:
        for rel, path in jobs:
            for attempt in range(ATTEMPTS):
                try:
                    result = _upload_one(ftp, base, rel, path)
                    break
                except NETWORK_ERRORS:
                    _quit(ftp)
                    time.sleep(2 * (attempt + 1))
                    ftp = _connect()
            else:
                failed.append(rel)
                continue
            if result == "skipped":
                skipped += 1
            else:
                done += 1
                with lock:
                    counter[0] += 1
                    if counter[0] % 200 == 0:
                        log.info("Загружено файлов: %d", counter[0])
    finally:
        _quit(ftp)
    return done, skipped, failed


def deploy(local_root, workers=None):
    local_root = Path(local_root)
    base = os.environ.get("FTP_PATH", "/").rstrip("/") or "/"
    workers = workers or int(os.environ.get("FTP_WORKERS", "4"))

    files = sorted((p.relative_to(local_root).as_posix(), p) for p in local_root.rglob("*") if p.is_file())
    wanted = {rel for rel, _ in files}

    # 1. Проверяем папку и заранее создаем подпапки одним коротким соединением
    ftp = _connect()
    try:
        try:
            ftp.cwd(base)
        except ftplib.error_perm:
            # диагностика без секретов: что видно от корня входа
            root_names = ftp.nlst()[:50]
            log.error("Папка FTP_PATH не найдена. Видно от корня входа: %s", ", ".join(root_names) or "(пусто)")
            raise
        for d in sorted({os.path.dirname(rel) for rel in wanted if os.path.dirname(rel)}):
            ftp.cwd(base)
            _cd_or_make(ftp, d)
    finally:
        _quit(ftp)

    # 2. Загрузка в несколько потоков, файлы раздаются по кругу для ровной нагрузки
    shares = [files[i::workers] for i in range(workers)]
    counter, lock = [0], threading.Lock()
    uploaded = skipped = 0
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for done, skip, bad in ex.map(lambda j: _upload_worker(j, base, counter, lock), shares):
            uploaded += done
            skipped += skip
            failed += bad

    if failed:
        log.error("Не загружено файлов: %d (например %s)", len(failed), failed[0])

    # 3. Чистка устаревшего: новое соединение (старое за это время закрывается сервером)
    removed = 0
    if not failed:
        ftp = _connect()
        try:
            for folder in MANAGED_FOLDERS:
                ftp.cwd(base)
                remote_files, dirs = _walk_remote(ftp, folder)
                for rel in remote_files:
                    if rel not in wanted:
                        ftp.cwd(base)
                        ftp.delete(rel)
                        removed += 1
                # пустые подпапки (от устаревших агентств или ЖК) тоже убираем: rmd сработает только на пустой
                for d in sorted(dirs, key=len, reverse=True):
                    ftp.cwd(base)
                    try:
                        ftp.rmd(d)
                    except ftplib.error_perm:
                        pass
        finally:
            _quit(ftp)

    log.info("Загрузка на хостинг: загружено %d, без изменений %d, удалено устаревших %d, ошибок %d (потоков %d)",
             uploaded, skipped, removed, len(failed), workers)
    if failed:
        raise RuntimeError(f"Не загружено файлов: {len(failed)}")
    return {"uploaded": uploaded, "skipped": skipped, "removed": removed}
