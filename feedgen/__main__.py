"""CLI генератора фидов.

  python -m feedgen build                    # собрать все фиды один раз (для cron)
  python -m feedgen build --agency pan       # только одно агентство
  python -m feedgen serve --port 8080        # сервис: раздача + автообновление
  python -m feedgen audit                    # аудит набора фидов перед публикацией
  python -m feedgen deploy                   # загрузить docs/ на хостинг (FTP_* из окружения)
  python -m feedgen validate docs/feeds/pan.xml
"""

import argparse
import json
import logging
import sys

from . import build
from .config import load_config
from .validate import validate_feed


def main():
    ap = argparse.ArgumentParser(prog="feedgen", description="Фиды ГК «ПСК» для агентств (YRL)")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sp = ap.add_subparsers(dest="cmd", required=True)

    b = sp.add_parser("build", help="собрать фиды")
    b.add_argument("--agency", action="append", help="slug агентства (можно несколько раз)")
    b.add_argument("--no-images", action="store_true", help="без конвертации планировок")

    s = sp.add_parser("serve", help="HTTP-сервер + автообновление")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)

    sp.add_parser("audit", help="аудит всех фидов перед публикацией")
    sp.add_parser("deploy", help="загрузить docs/ на хостинг по FTP")

    v = sp.add_parser("validate", help="проверить готовый фид")
    v.add_argument("path")

    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if args.cmd == "validate":
        res = validate_feed(args.path)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 1 if res["errors"] else 0

    cfg = load_config(args.config)
    if args.cmd == "build":
        report = build.run(cfg, only=args.agency, with_images=not args.no_images)
        return 1 if any(f["status"] != "ok" for f in report["feeds"]) else 0

    if args.cmd == "audit":
        from . import audit
        return 1 if audit.run(cfg)["status"] != "ok" else 0

    if args.cmd == "deploy":
        from .deploy import deploy
        deploy(cfg.output_dir)
        return 0

    if args.cmd == "serve":
        from .server import serve
        serve(cfg, lambda: load_config(args.config), args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
