"""Режим сервиса: раздает docs/ по HTTP и перегенерирует фиды по расписанию.

Для продакшена удобнее поставить перед ним nginx с HTTPS (см. deploy/nginx.conf),
либо отдавать docs/ самим nginx, а генерацию запускать по cron (`build`).
"""

import functools
import json
import logging
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import build

log = logging.getLogger(__name__)
state = {"last_ok": None, "last_error": None, "running": False}


def refresh_loop(cfg_loader, interval_min):
    while True:
        state["running"] = True
        try:
            cfg = cfg_loader()           # конфиг перечитывается на каждом цикле
            build.run(cfg)
            state["last_ok"] = time.strftime("%Y-%m-%d %H:%M:%S")
            state["last_error"] = None
        except Exception as e:
            log.exception("Ошибка генерации, старые фиды остаются на месте")
            state["last_error"] = str(e)
        finally:
            state["running"] = False
        time.sleep(interval_min * 60)


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".xml": "application/xml; charset=utf-8"}

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            body = json.dumps(state, ensure_ascii=False).encode()
            self.send_response(200 if not state["last_error"] else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def list_directory(self, path):          # не показываем листинг папок
        self.send_error(404)

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)


def serve(cfg, cfg_loader, host, port):
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=refresh_loop, args=(cfg_loader, cfg.refresh_minutes), daemon=True).start()
    handler = functools.partial(Handler, directory=str(cfg.output_dir))
    log.info("Сервер: http://%s:%d  (перегенерация каждые %d мин)", host, port, cfg.refresh_minutes)
    ThreadingHTTPServer((host, port), handler).serve_forever()
