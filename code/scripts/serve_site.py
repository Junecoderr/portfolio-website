"""Serve the built site (dist/site) locally with the same headers as Vercel.

    uv run python scripts/serve_site.py [--dir dist/site] [--port 8000]

Unlike `python -m http.server`, this sends the headers from vercel.json
(the CSP and the COOP/COEP pair that make the page cross-origin isolated, so
the playground gets SharedArrayBuffer for inline ask() answers and an
instant Stop) and handles cleanUrls (/playground serves playground.html).
Standard library only.
"""
from __future__ import annotations

import argparse
import functools
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def vercel_headers(config: dict) -> list[tuple[re.Pattern, list[tuple[str, str]]]]:
    """vercel.json's header rules as (compiled source pattern, headers)."""
    rules = []
    for rule in config.get("headers", []):
        # Vercel's "(.*)" groups are regular expressions; anchor the whole path.
        rules.append((re.compile("^" + rule["source"] + "$"),
                      [(h["key"], h["value"]) for h in rule["headers"]]))
    return rules


def headers_for(path: str, rules) -> list[tuple[str, str]]:
    out: dict[str, str] = {}
    for pattern, headers in rules:
        if pattern.match(path):
            out.update(dict(headers))
    return list(out.items())


class Handler(SimpleHTTPRequestHandler):
    rules: list = []

    def translate_path(self, path):
        local = super().translate_path(path)
        p = Path(local)
        if not p.exists() and p.suffix == "" and p.with_suffix(".html").is_file():
            return str(p.with_suffix(".html"))  # cleanUrls
        return local

    def end_headers(self):
        for key, value in headers_for(self.path.split("?", 1)[0], self.rules):
            self.send_header(key, value)
        super().end_headers()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(ROOT / "dist" / "site"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    Handler.rules = vercel_headers(json.loads((ROOT / "vercel.json").read_text(encoding="utf-8")))
    server = ThreadingHTTPServer((args.host, args.port), functools.partial(Handler, directory=args.dir))
    print(f"Serving {Path(args.dir).as_posix()} at http://{args.host}:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
