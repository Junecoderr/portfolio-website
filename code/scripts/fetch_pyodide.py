"""Download the pinned Pyodide core files into site/vendor/pyodide/.

The TB website runs TB programs in the browser with Pyodide (Python compiled
to WebAssembly). We vendor it so the site works offline and loads nothing
from a CDN at runtime.

Usage:
    uv run python scripts/fetch_pyodide.py

Only the standard library is used. Every file is checked against a pinned
SHA-256 hash; files that are already present and valid are skipped.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

PYODIDE_VERSION = "0.27.7"
BASE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"

# What pyodide.js needs at load time: itself, the Emscripten glue
# (pyodide.asm.js + .wasm), the standard library zip and the package lock
# file (fetched by loadPyodide even when no packages are installed).
# pyodide.mjs is the ES-module flavour of pyodide.js, kept for completeness.
FILES = {
    "pyodide.js": "b4cb23a53aba19c221659b9fb40a2f18281d685691dc06647fb0afd0681baf98",
    "pyodide.mjs": "04c052c815abb0bf2981fa38c2dfa58e3120ee5288c956df2b2a15e301782c5f",
    "pyodide.asm.js": "6b4c90de5b7172873f04f21884d0e9d2274e305fe32116558fe3e4fbe3618d51",
    "pyodide.asm.wasm": "a50dd1843f805a0b7c45b61037ee0d7b26dfe85efe0e18ef95a34ad24e401f5f",
    "python_stdlib.zip": "16611534726e5d8ac2bd8f926410b2dcb8d6f49aa24913463533b457a2115c16",
    "pyodide-lock.json": "9c45b916001a750f4102fc287494f3eab215909c7535c626a20db80fd6333e2c",
}

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "site" / "vendor" / "pyodide"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str, expected: str) -> None:
    target = DEST / name
    if target.exists() and sha256(target) == expected:
        print(f"  ok       {name}")
        return
    url = BASE_URL + name
    print(f"  download {name} ...", flush=True)
    tmp = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "tb-lang fetch_pyodide"})
    with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    actual = sha256(tmp)
    if actual != expected:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"SHA-256 mismatch for {name}\n  expected {expected}\n  got      {actual}\n"
            "Refusing to use it. Check your network or the pinned hash."
        )
    tmp.replace(target)


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"Pyodide {PYODIDE_VERSION} -> {DEST.relative_to(ROOT)}")
    for name, expected in FILES.items():
        fetch(name, expected)
    print("Done. All files verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
