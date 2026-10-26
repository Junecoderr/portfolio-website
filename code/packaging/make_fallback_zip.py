"""Build dist/tb-python.zip: the "run TB with your own Python" download.

Usage (from the repo root):
    uv run python packaging/make_fallback_zip.py [--dist dist]

Contents (flat, so "Extract all" on Windows gives one tidy folder):
    tb.py
    welcome.tb
    tbcore/...         the language itself (lexer, parser, compiler, VM, ...)
    tbtools/...        the `tb fmt`, `tb check`, `tb build`, ... commands
    tblib/...          the standard library (needed by `import ...`), incl. tblib/data/
    examples/*.tb      (whatever exists at build time)
    apps/...           the example apps and their sample data
    packages/...       example TB packages (tb add ...)
    spec/...           the language spec and its conformance tests (tb test spec)
    README.txt         (from packaging/README-python.txt, Windows line endings)
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "tb-python.zip"
FIXED_TIME = (2024, 1, 1, 0, 0, 0)  # stable timestamps -> reproducible zip


def add(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_TIME)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


SKIP_DIRS = {"__pycache__", ".pytest_cache", "dist"}
# Data an app saved while being tried out on the build machine.
SKIP_NAMES = {"todo.json", "journal.json", ".DS_Store"}
FOLDERS = ("tbcore", "tbtools", "tblib", "apps", "packages", "spec")
SKIP_SUFFIXES = {".pyc", ".pyo"}


def tree(folder: Path) -> list[Path]:
    """Every file under folder (sorted), minus caches and hidden files."""
    files = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and path.suffix not in SKIP_SUFFIXES and path.name not in SKIP_NAMES:
            files.append(path)
    return files


def crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dist/tb-python.zip")
    parser.add_argument("--dist", default=str(ROOT / "dist"))
    args = parser.parse_args()
    dist = Path(args.dist).resolve()
    dist.mkdir(parents=True, exist_ok=True)
    out = dist / NAME

    required = [ROOT / "tb.py", ROOT / "welcome.tb", ROOT / "packaging" / "README-python.txt",
                ROOT / "tblib" / "__init__.py", ROOT / "tbcore" / "__init__.py",
                ROOT / "tbtools" / "__init__.py"]
    for path in required:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1

    examples = sorted((ROOT / "examples").glob("*.tb"))
    with zipfile.ZipFile(out, "w") as zf:
        add(zf, "tb.py", (ROOT / "tb.py").read_bytes())
        add(zf, "welcome.tb", (ROOT / "welcome.tb").read_bytes())
        for ex in examples:
            add(zf, f"examples/{ex.name}", ex.read_bytes())
        for folder in FOLDERS:
            for path in tree(ROOT / folder):
                add(zf, path.relative_to(ROOT).as_posix(), path.read_bytes())
        add(zf, "README.txt", crlf((ROOT / "packaging" / "README-python.txt").read_text(encoding="utf-8")))

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    print(f"wrote {out} ({len(names)} files, {out.stat().st_size / 1000:.0f} kB, {len(examples)} examples)")
    for top in sorted({n.split("/", 1)[0] for n in names}):
        count = sum(1 for n in names if n == top or n.startswith(top + "/"))
        print(f"  {top}{'/' if count > 1 or '.' not in top else ''}  ({count} file{'s' if count != 1 else ''})")

    # Keep dist/SHA256SUMS.txt in step with the new zip.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build import write_checksums

    write_checksums(dist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
