"""_magic: work out what kind of file some bytes are, from their first bytes.

Shared by `binary.file_type`, `pe`, `elf`, `scan` and the file-inspector apps.
`sniff(data)` is pure Python over bytes; `file_type(line, path)` is the TB-facing
wrapper (friendly errors). Nothing here depends on the file extension.
"""

from __future__ import annotations

import os
import struct

from tblib._api import check_type, fail, fail_os

SNIFF_BYTES = 8192

# (type, description, mime, extension)
_SIGNATURES = [
    (b"\x7fELF", "elf", "Linux/Unix executable (ELF)", "application/x-executable", ""),
    (b"%PDF", "pdf", "PDF document", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "png", "PNG image", "image/png", ".png"),
    (b"\xff\xd8\xff", "jpeg", "JPEG image", "image/jpeg", ".jpg"),
    (b"GIF87a", "gif", "GIF image", "image/gif", ".gif"),
    (b"GIF89a", "gif", "GIF image", "image/gif", ".gif"),
    (b"BM", "bmp", "Bitmap image", "image/bmp", ".bmp"),
    (b"\x1f\x8b", "gzip", "gzip compressed data", "application/gzip", ".gz"),
    (b"BZh", "bzip2", "bzip2 compressed data", "application/x-bzip2", ".bz2"),
    (b"\xfd7zXZ\x00", "xz", "xz compressed data", "application/x-xz", ".xz"),
    (b"7z\xbc\xaf\x27\x1c", "7z", "7-Zip archive", "application/x-7z-compressed", ".7z"),
    (b"Rar!\x1a\x07", "rar", "RAR archive", "application/vnd.rar", ".rar"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole", "Microsoft OLE compound file (old Office document, MSI)",
     "application/x-ole-storage", ""),
    (b"{\\rtf", "rtf", "Rich Text document", "application/rtf", ".rtf"),
    (b"SQLite format 3\x00", "sqlite", "SQLite database", "application/vnd.sqlite3", ".sqlite"),
    (b"ElfFile\x00", "evtx", "Windows event log (EVTX)", "application/x-ms-evtx", ".evtx"),
    (b"\x0a\x0d\x0d\x0a", "pcapng", "Network capture (pcapng)", "application/x-pcapng", ".pcapng"),
    (b"\xa1\xb2\xc3\xd4", "pcap", "Network capture (pcap, big-endian)", "application/vnd.tcpdump.pcap", ".pcap"),
    (b"\xd4\xc3\xb2\xa1", "pcap", "Network capture (pcap)", "application/vnd.tcpdump.pcap", ".pcap"),
    (b"\xa1\xb2\x3c\x4d", "pcap", "Network capture (pcap, nanosecond, big-endian)", "application/vnd.tcpdump.pcap", ".pcap"),
    (b"\x4d\x3c\xb2\xa1", "pcap", "Network capture (pcap, nanosecond)", "application/vnd.tcpdump.pcap", ".pcap"),
    (b"L\x00\x00\x00\x01\x14\x02\x00", "lnk", "Windows shortcut (LNK)", "application/x-ms-shortcut", ".lnk"),
    (b"OggS", "ogg", "Ogg media", "audio/ogg", ".ogg"),
    (b"ID3", "mp3", "MP3 audio", "audio/mpeg", ".mp3"),
    (b"fLaC", "flac", "FLAC audio", "audio/flac", ".flac"),
    (b"\x00\x00\x01\x00", "ico", "Windows icon", "image/x-icon", ".ico"),
    (b"wOFF", "woff", "Web font (WOFF)", "font/woff", ".woff"),
    (b"wOF2", "woff2", "Web font (WOFF2)", "font/woff2", ".woff2"),
    (b"MSCF", "cab", "Windows cabinet archive", "application/vnd.ms-cab-compressed", ".cab"),
    (b"\x1a\x45\xdf\xa3", "matroska", "Matroska/WebM media", "video/x-matroska", ".mkv"),
    (b"!<arch>\n", "ar", "Unix archive (ar, .deb)", "application/x-archive", ".a"),
    (b"\x00asm", "wasm", "WebAssembly module", "application/wasm", ".wasm"),
    (b"dex\n", "dex", "Android Dalvik executable", "application/x-dex", ".dex"),
    (b"\x28\xb5\x2f\xfd", "zstd", "Zstandard compressed data", "application/zstd", ".zst"),
    (b"CWS", "swf", "Flash movie (compressed)", "application/x-shockwave-flash", ".swf"),
    (b"FWS", "swf", "Flash movie", "application/x-shockwave-flash", ".swf"),
    (b"\xef\xbb\xbf", "text", "Text (UTF-8 with byte order mark)", "text/plain", ".txt"),
    (b"\xff\xfe", "text", "Text (UTF-16 little-endian)", "text/plain", ".txt"),
    (b"\xfe\xff", "text", "Text (UTF-16 big-endian)", "text/plain", ".txt"),
]

_MACHO = {
    b"\xfe\xed\xfa\xce": "Mach-O executable (32-bit, big-endian)",
    b"\xce\xfa\xed\xfe": "Mach-O executable (32-bit)",
    b"\xfe\xed\xfa\xcf": "Mach-O executable (64-bit, big-endian)",
    b"\xcf\xfa\xed\xfe": "Mach-O executable (64-bit)",
}

_ZIP_KINDS = [
    (b"word/", "docx", "Word document (OOXML)", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    (b"xl/", "xlsx", "Excel workbook (OOXML)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    (b"ppt/", "pptx", "PowerPoint presentation (OOXML)", "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    (b"META-INF/MANIFEST.MF", "jar", "Java archive (JAR)", "application/java-archive", ".jar"),
    (b"AndroidManifest.xml", "apk", "Android package (APK)", "application/vnd.android.package-archive", ".apk"),
    (b"classes.dex", "apk", "Android package (APK)", "application/vnd.android.package-archive", ".apk"),
]

_TEXT_BYTES = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D, 0x0C, 0x1B}


def _result(kind, description, mime, extension, **extra):
    out = {"type": kind, "description": description, "mime": mime, "extension": extension,
           "executable": kind in ("pe", "elf", "macho", "macho-fat", "dex", "wasm", "script", "lnk", "apk")}
    out.update(extra)
    return out


def _is_pe(data: bytes):
    if len(data) < 0x40:
        return None
    (e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
    if e_lfanew + 6 <= len(data) and data[e_lfanew:e_lfanew + 4] == b"PE\x00\x00":
        machine, = struct.unpack_from("<H", data, e_lfanew + 4)
        characteristics = struct.unpack_from("<H", data, e_lfanew + 22)[0] if e_lfanew + 24 <= len(data) else 0
        dll = bool(characteristics & 0x2000)
        arch = {0x14C: "x86", 0x8664: "x64", 0x1C0: "arm", 0xAA64: "arm64", 0x200: "ia64"}.get(machine, hex(machine))
        return _result("pe", f"Windows {'DLL' if dll else 'executable'} (PE, {arch})",
                       "application/vnd.microsoft.portable-executable", ".dll" if dll else ".exe",
                       architecture=arch)
    if data[0x18:0x1A] == b"\x40\x00" or data[0x3C:0x40] == b"\x00\x00\x00\x00":
        return _result("mz", "DOS executable (MZ)", "application/x-dosexec", ".exe")
    return _result("mz", "DOS executable (MZ, no PE header in the first bytes)", "application/x-dosexec", ".exe")


def _looks_like_text(data: bytes):
    sample = data[:SNIFF_BYTES]
    if not sample:
        return None
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        # Allow a cut-off multibyte character at the end of the sample.
        try:
            text = sample[:-3].decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is None:
        if all(b in _TEXT_BYTES for b in sample):
            text = sample.decode("latin-1")
        else:
            return None
    if "\x00" in text:
        return None
    control = sum(1 for ch in text if ord(ch) < 0x20 and ch not in "\t\n\r\x0c\x1b")
    if control > len(text) * 0.02:
        return None
    stripped = text.lstrip()
    low = stripped[:200].lower()
    if stripped.startswith("#!"):
        first = stripped.splitlines()[0]
        return _result("script", f"Script ({first.strip()})", "text/x-script", "", executable=True)
    if low.startswith("<?xml") or low.startswith("<svg"):
        return _result("xml", "XML document", "application/xml", ".xml")
    if low.startswith("<!doctype html") or low.startswith("<html") or "<html" in low:
        return _result("html", "HTML page", "text/html", ".html")
    if stripped[:1] in ("{", "[") and stripped.rstrip()[-1:] in ("}", "]"):
        return _result("json", "JSON data", "application/json", ".json")
    if low.startswith("-----begin "):
        return _result("pem", "PEM-encoded key or certificate", "application/x-pem-file", ".pem")
    if low.startswith("%!ps"):
        return _result("postscript", "PostScript", "application/postscript", ".ps")
    lines = text.splitlines()
    if len(lines) >= 2 and all(line.count(",") >= 1 for line in lines[:5]) and \
            len({line.count(",") for line in lines[:5]}) == 1:
        return _result("csv", "Comma-separated text (CSV)", "text/csv", ".csv")
    return _result("text", "Plain text", "text/plain", ".txt")


def sniff(data: bytes) -> dict:
    """What kind of file these (first) bytes come from: a map with "type"
    (a short lowercase name like "pe", "elf", "pdf", "zip", "text",
    "unknown"), "description", "mime", "extension" and "executable"."""
    if not data:
        return _result("empty", "Empty file", "application/x-empty", "")
    if data[:2] == b"MZ":
        found = _is_pe(data)
        if found:
            return found
    if data[:4] in _MACHO:
        return _result("macho", _MACHO[data[:4]], "application/x-mach-binary", "")
    if data[:4] == b"\xca\xfe\xba\xbe":
        count = struct.unpack_from(">I", data, 4)[0] if len(data) >= 8 else 0
        if count < 0x30:
            return _result("macho-fat", "Mach-O universal binary", "application/x-mach-binary", "")
        return _result("java-class", "Java class file", "application/java-vm", ".class")
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        head = data[:SNIFF_BYTES]
        for marker, kind, description, mime, ext in _ZIP_KINDS:
            if marker in head:
                return _result(kind, description, mime, ext)
        if b"mimetypeapplication/epub" in head:
            return _result("epub", "EPUB e-book", "application/epub+zip", ".epub")
        return _result("zip", "ZIP archive", "application/zip", ".zip")
    for magic, kind, description, mime, ext in _SIGNATURES:
        if data.startswith(magic):
            if kind == "text":
                encoding = "utf-8-sig" if magic == b"\xef\xbb\xbf" else "utf-16"
                decoded = data[:SNIFF_BYTES].decode(encoding, "replace").encode("utf-8")
                guess = _looks_like_text(decoded) or _result("text", "Plain text", mime, ext)
                if guess["type"] != "text":
                    description = f"{description}, {guess['description']}"
                return _result(guess["type"], description, guess["mime"], guess["extension"])
            return _result(kind, description, mime, ext)
    if data[:4] == b"RIFF" and len(data) >= 12:
        form = data[8:12]
        if form == b"WAVE":
            return _result("wav", "WAV audio", "audio/wav", ".wav")
        if form == b"AVI ":
            return _result("avi", "AVI video", "video/x-msvideo", ".avi")
        if form == b"WEBP":
            return _result("webp", "WebP image", "image/webp", ".webp")
        return _result("riff", "RIFF container", "application/octet-stream", "")
    if data[4:8] == b"ftyp":
        return _result("mp4", "MP4/QuickTime media", "video/mp4", ".mp4")
    if data[:5] == b"ustar" or data[257:262] == b"ustar":
        return _result("tar", "tar archive", "application/x-tar", ".tar")
    if data[:3] == b"\x00\x00\x00" and data[3:4] in (b"\x18", b"\x1c", b"\x20") and data[4:8] == b"ftyp":
        return _result("mp4", "MP4 media", "video/mp4", ".mp4")
    text = _looks_like_text(data)
    if text:
        return text
    return _result("unknown", "Unknown binary data", "application/octet-stream", "")


def read_head(line, path, fn="binary.file_type", size=SNIFF_BYTES) -> bytes:
    check_type(line, path, "text", fn, "path")
    if path == "":
        fail(line, f"{fn}() needs a path, but got empty text.")
    full = os.path.expanduser(path)
    if os.path.isdir(full):
        fail(line, f"'{path}' is a folder, not a file.", path=path)
    try:
        with open(full, "rb") as f:
            return f.read(size)
    except OSError as err:
        fail_os(line, err, path, "read")


def file_type(line, path) -> dict:
    """The TB-facing helper: sniff a file on disk. Adds "path" and "size"."""
    if isinstance(path, (bytes, bytearray)):
        return sniff(bytes(path))
    data = read_head(line, path)
    result = sniff(data)
    result["path"] = path
    try:
        result["size"] = os.path.getsize(os.path.expanduser(path))
    except OSError:
        result["size"] = len(data)
    return result


def entropy(data: bytes) -> float:
    """Shannon entropy of some bytes, 0.0 (all the same) to 8.0 (random)."""
    if not data:
        return 0.0
    import math
    from collections import Counter

    counts = Counter(data)  # counts in C, fast even for big files
    total = len(data)
    result = 0.0
    for c in counts.values():
        if c:
            p = c / total
            result -= p * math.log2(p)
    return round(result, 3)


def to_bytes(line, value, fn: str, arg: str = "data") -> bytes:
    """Accept bytes, a bytearray, a list of small whole numbers or text (UTF-8)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8", "surrogatepass")
    if isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 256 for v in value):
        return bytes(value)
    from tblib._api import type_name

    fail(line, f"{fn}() needs {arg} to be bytes or text, not {type_name(value)}.")


def read_file(line, path, fn: str, max_size: int | None = None) -> bytes:
    """The whole file as bytes, with friendly errors."""
    check_type(line, path, "text", fn, "path")
    if path == "":
        fail(line, f"{fn}() needs a path, but got empty text.")
    full = os.path.expanduser(path)
    if os.path.isdir(full):
        fail(line, f"'{path}' is a folder, not a file.", path=path)
    try:
        if max_size is not None and os.path.getsize(full) > max_size:
            fail(line, f"'{path}' is bigger than {max_size:,} bytes; {fn}() reads whole files, "
                       "so pick a smaller one.", path=path)
        with open(full, "rb") as f:
            return f.read()
    except OSError as err:
        fail_os(line, err, path, "read")
