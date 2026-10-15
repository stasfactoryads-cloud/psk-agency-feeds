"""Загрузка docs/ на хостинг по FTP (или FTPS). Используется в GitHub Actions.

Настройки берутся из переменных окружения (секреты репозитория):
  FTP_SERVER, FTP_USERNAME, FTP_PASSWORD  — доступ
  FTP_PATH                                — папка на хостинге, куда кладутся файлы
  FTP_TLS                                 — "1" для FTPS (по умолчанию), "0" для обычного FTP
  FTP_PORT                                — порт, по умолчанию 21

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


def deploy(local_root):
    local_root = Path(local_root)
    base = os.environ.get("FTP_PATH", "/").rstrip("/") or "/"
    ftp = _connect()
    ftp.cwd(base)

    wanted, uploaded, skipped = set(), 0, 0
    for path in sorted(p for p in local_root.rglob("*") if p.is_file()):
        rel = path.relative_to(local_root).as_posix()
        wanted.add(rel)
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
        uploaded += 1

    removed = 0
    for folder in MANAGED_FOLDERS:
        ftp.cwd(base)
        files, dirs = _walk_remote(ftp, folder)
        for rel in files:
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
    log.info("Загрузка на хостинг: загружено %d, без изменений %d, удалено устаревших %d",
             uploaded, skipped, removed)
    return {"uploaded": uploaded, "skipped": skipped, "removed": removed}
