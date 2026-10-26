"""Make packaging/windows/tb.ico (the .tb file-type and tb.exe icon) from the
site's favicon, site/assets/img/favicon.svg.

Usage (from code/; needs rsvg-convert, e.g. `brew install librsvg`):
    uv run python packaging/windows/make_icon.py

The result is committed, so builds (CI) need neither this script nor
rsvg-convert. Re-run it only when the favicon changes.

The .ico holds PNG images at the sizes Windows asks for (16-256 px); PNG
entries inside .ico files are supported since Windows Vista. Written with the
standard library only (struct), no Pillow.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent.parent
SVG = CODE / "site" / "assets" / "img" / "favicon.svg"
ICO = Path(__file__).resolve().parent / "tb.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(size: int, out: Path) -> bytes:
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(out), str(SVG)], check=True)
    data = out.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"rsvg-convert did not write a PNG for {size}px")
    return data


def pack(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type 1 = icon, count
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


def main() -> int:
    if not shutil.which("rsvg-convert"):
        print("rsvg-convert not found (brew install librsvg / apt install librsvg2-bin)", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        images = [(s, render(s, Path(tmp) / f"{s}.png")) for s in SIZES]
    ICO.write_bytes(pack(images))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, sizes {SIZES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
