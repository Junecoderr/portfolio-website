"""evtx: read binary Windows event logs (.evtx) into the same records logs gives.

EVTX fidelity (what is and isn't handled, honestly):

* Handled: the file header (version, chunk count, dirty/full flags, CRC32),
  every 64 KB chunk found in the file (also ones past a stale header count),
  chunk header and records CRC32 checks, event records (0x2a2a), binary XML
  tokens (elements with and without attributes, values, normal and optional
  substitutions, CDATA, character and entity references, processing
  instructions), inline and shared element names, resident and referenced
  templates (cached per chunk), substitution arrays with every value type in
  the format (strings, ANSI, signed/unsigned ints, float/double, bool,
  binary, GUID, FILETIME, SYSTEMTIME, SID, HexInt32/64, arrays, nested
  BinXml used for EventData/UserData). Broken records become placeholders
  with "broken": true unless options {"strict": true}. Files are streamed a
  chunk at a time (also from .gz and one-file .zip).
* Damage: in a file marked dirty, whole records past a chunk's stale free
  space offset still count. Unknown binary XML tokens are skipped to the end
  of their element (whose size is known) and the record is marked "partial".
  options {"recover": true} carves records from chunk slack space (and from
  chunks whose header signature is gone), marked "recovered".
* Names: task names for the Security auditing provider, the standard opcode
  names, and whatever a RenderingInfo block (forwarded events) carries.
* Not handled: the event *message* text. Windows renders it from the
  provider's message DLL, which isn't in the file, so records get the
  description from EVENTS, keyed by channel + event ID (Security, System,
  Sysmon, PowerShell), or "Event N".
* Tested against synthetic files from tests/lib/evtxwriter.py, a fuzz test of
  damaged copies, and (in CI on Windows) the runner's own exported logs
  compared with wevtutil's rendering: tests/lib/test_evtx_windows.py.
"""

from __future__ import annotations

import datetime as dt
import io
import json as _json
import struct
import uuid
import zlib
from xml.sax.saxutils import escape, quoteattr

from tblib import _magic, logs
from tblib._api import Module, check_type, fail

MAX_RECORDS = 500_000

SECURITY_PROVIDER = "Microsoft-Windows-Security-Auditing"
SYSMON = "microsoft-windows-sysmon/operational"
POWERSHELL = "microsoft-windows-powershell/operational"
# (lowercased channel, event id) -> (action, description). Sysmon titles come from
# logs.SYSMON_EVENTS, PowerShell ones from Microsoft's PowerShell logging docs.
EVENTS = {(("system" if i in logs.SYSTEM_IDS else "security"), i): (action, text)
          for i, (action, text, _success) in logs.EVENTS.items()}
EVENTS.update({(SYSMON, i): v for i, v in logs.SYSMON_EVENTS.items()})
EVENTS.update({(POWERSHELL, i): v for i, v in {
    4103: ("powershell_pipeline", "Module logging: PowerShell ran a pipeline (command and parameters)"),
    4104: ("powershell_script_block", "Script block logging: PowerShell compiled a script block (its text)"),
    4105: ("powershell_script_started", "A PowerShell script block started"),
    4106: ("powershell_script_finished", "A PowerShell script block finished"),
}.items()})


def describe(record, channel):
    """Set action/description from EVENTS for this channel + event ID."""
    channel = str(channel or "").lower()
    known = EVENTS.get((channel, record.get("event_id")))
    old = record.get("description")
    if known:
        record["action"], record["description"] = known
    if old and record.get("message") == old:
        record["message"] = None  # build_record fills it from the new description
CHUNK_SIZE = 65536
FILETIME_EPOCH = 116444736000000000

LEVELS = {0: "LogAlways", 1: "Critical", 2: "Error", 3: "Warning", 4: "Information", 5: "Verbose"}
# Standard opcodes every provider shares (winmeta.xml); providers' own opcodes start at 10.
OPCODES = {0: "Info", 1: "Start", 2: "Stop", 3: "DCStart", 4: "DCStop", 5: "Extension", 6: "Reply",
           7: "Resume", 8: "Suspend", 9: "Send", 240: "Receive"}
# Task categories of Microsoft-Windows-Security-Auditing: the audit subcategory names Event Viewer shows.
SECURITY_TASKS = {
    12288: "Security State Change", 12289: "Security System Extension", 12290: "System Integrity",
    12291: "IPsec Driver", 12292: "Other System Events", 12544: "Logon", 12545: "Logoff",
    12546: "Account Lockout", 12547: "IPsec Main Mode", 12548: "Special Logon", 12549: "IPsec Quick Mode",
    12550: "IPsec Extended Mode", 12551: "Other Logon/Logoff Events", 12552: "Network Policy Server",
    12553: "User / Device Claims", 12554: "Group Membership", 12800: "File System", 12801: "Registry",
    12802: "Kernel Object", 12803: "SAM", 12804: "Other Object Access Events", 12805: "Certification Services",
    12806: "Application Generated", 12807: "Handle Manipulation", 12808: "File Share",
    12809: "Filtering Platform Packet Drop", 12810: "Filtering Platform Connection", 12811: "Detailed File Share",
    12812: "Removable Storage", 12813: "Central Policy Staging", 13056: "Sensitive Privilege Use",
    13057: "Non Sensitive Privilege Use", 13058: "Other Privilege Use Events", 13312: "Process Creation",
    13313: "Process Termination", 13314: "DPAPI Activity", 13315: "RPC Events", 13316: "Plug and Play Events",
    13317: "Token Right Adjusted Events", 13568: "Audit Policy Change", 13569: "Authentication Policy Change",
    13570: "Authorization Policy Change", 13571: "MPSSVC Rule-Level Policy Change",
    13572: "Filtering Platform Policy Change", 13573: "Other Policy Change Events",
    13824: "User Account Management", 13825: "Computer Account Management", 13826: "Security Group Management",
    13827: "Distribution Group Management", 13828: "Application Group Management",
    13829: "Other Account Management Events", 14080: "Directory Service Access", 14081: "Directory Service Changes",
    14082: "Directory Service Replication", 14083: "Detailed Directory Service Replication",
    14336: "Credential Validation", 14337: "Kerberos Service Ticket Operations", 14338: "Other Account Logon Events",
    14339: "Kerberos Authentication Service",
}
KEYWORD_NAMES = {0x8020000000000000: "Audit Success", 0x8010000000000000: "Audit Failure",
                 0x8080000000000000: "Classic", 0x8000000000000000: "None"}


class EvtxFormat(Exception):
    pass


class UnknownToken(EvtxFormat):
    """A binary XML token (or inline value type) this reader doesn't know; its size is unknown."""


MAX_DEPTH = 64  # nested elements and templates; real events stay far below this


# =============================================================================
# Value decoding
# =============================================================================

def filetime_text(value: int):
    if value <= 0:
        return None
    micros = (value - FILETIME_EPOCH) // 10
    try:
        when = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=micros)
    except OverflowError:
        return None
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond:06d}0Z"


def filetime_stamp(value: int):
    if value <= 0:
        return None
    return round((value - FILETIME_EPOCH) / 10_000_000, 6)


def sid_text(raw: bytes) -> str:
    if len(raw) < 8:
        return raw.hex()
    revision, count = raw[0], raw[1]
    authority = int.from_bytes(raw[2:8], "big")
    subs = [int.from_bytes(raw[8 + 4 * i:12 + 4 * i], "little") for i in range(min(count, (len(raw) - 8) // 4))]
    return "S-" + "-".join(str(x) for x in [revision, authority] + subs)


def guid_text(raw: bytes) -> str:
    return "{" + str(uuid.UUID(bytes_le=bytes(raw[:16]))) + "}"


def systemtime_text(raw: bytes):
    if len(raw) < 16:
        return None
    y, mo, _dow, d, h, mi, s, ms = struct.unpack_from("<8H", raw, 0)
    try:
        return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}.{ms:03d}Z"
    except ValueError:
        return None


def decode_value(kind: int, raw: bytes, parser=None):
    """One substitution value (kind is the value type byte)."""
    if kind & 0x80 and kind != 0x81:
        base = kind & 0x7F
        size = {0x03: 1, 0x04: 1, 0x05: 2, 0x06: 2, 0x07: 4, 0x08: 4, 0x09: 8, 0x0A: 8, 0x0B: 4, 0x0C: 8,
                0x0D: 4, 0x0F: 16, 0x11: 8, 0x12: 16, 0x14: 4, 0x15: 8}.get(base)
        if not size:
            return raw.hex()
        return [decode_value(base, raw[i:i + size]) for i in range(0, len(raw) - size + 1, size)]
    if kind == 0x00:
        return None
    if kind == 0x01:
        text = raw.decode("utf-16-le", "replace")
        return text.rstrip("\x00")
    if kind == 0x02:
        return raw.decode("latin-1").rstrip("\x00")
    if kind in (0x03, 0x05, 0x07, 0x09):
        return int.from_bytes(raw, "little", signed=True) if raw else 0
    if kind in (0x04, 0x06, 0x08, 0x0A, 0x10):
        return int.from_bytes(raw, "little") if raw else 0
    if kind == 0x0B and len(raw) >= 4:
        return struct.unpack_from("<f", raw, 0)[0]
    if kind == 0x0C and len(raw) >= 8:
        return struct.unpack_from("<d", raw, 0)[0]
    if kind == 0x0D:
        return bool(int.from_bytes(raw, "little")) if raw else False
    if kind == 0x0E:
        return raw.hex()
    if kind == 0x0F and len(raw) >= 16:
        return guid_text(raw)
    if kind == 0x11 and len(raw) >= 8:
        return filetime_text(struct.unpack_from("<Q", raw, 0)[0])
    if kind == 0x12:
        return systemtime_text(raw)
    if kind == 0x13:
        return sid_text(raw)
    if kind == 0x14:
        return "0x%x" % int.from_bytes(raw, "little") if raw else "0x0"
    if kind == 0x15:
        return "0x%x" % int.from_bytes(raw, "little") if raw else "0x0"
    if kind == 0x21:
        if parser is None:
            return raw.hex()
        return parser.parse_fragment(raw)
    if kind == 0x81:
        text = raw.decode("utf-16-le", "replace")
        return [part for part in text.split("\x00") if part]
    return raw.hex()


# =============================================================================
# Binary XML
# =============================================================================

def xml_str(value) -> str:
    """A value as Windows writes it in event XML (booleans are true/false)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Element:
    __slots__ = ("name", "attrs", "children")

    def __init__(self, name):
        self.name = name
        self.attrs = {}
        self.children = []  # Elements or text values

    def text(self):
        parts = []
        for c in self.children:
            if isinstance(c, Element):
                continue
            if isinstance(c, list):
                parts.append(", ".join(xml_str(x) for x in c))
            elif c is not None:
                parts.append(xml_str(c))
        return "".join(parts)

    def find(self, name):
        for c in self.children:
            if isinstance(c, Element) and c.name == name:
                return c
        return None

    def to_xml(self) -> str:
        attrs = "".join(f" {k}={quoteattr(xml_str(v))}" for k, v in self.attrs.items() if v is not None)
        inner = ""
        for c in self.children:
            if isinstance(c, Element):
                inner += c.to_xml()
            elif isinstance(c, list):
                inner += escape(", ".join(xml_str(x) for x in c))
            elif c is not None:
                inner += escape(xml_str(c))
        if not inner:
            return f"<{self.name}{attrs}/>"
        return f"<{self.name}{attrs}>{inner}</{self.name}>"


class Substitution:
    __slots__ = ("index", "kind", "optional")

    def __init__(self, index, kind, optional):
        self.index, self.kind, self.optional = index, kind, optional


class ChunkParser:
    """Parses binary XML inside one chunk (names and templates are chunk-relative)."""

    def __init__(self, chunk: bytes):
        self.chunk = chunk
        self.names = {}
        self.templates = {}
        self.depth = 0
        self.skipped = 0  # unknown tokens skipped in the current record

    def enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise EvtxFormat(f"elements or templates nest deeper than {MAX_DEPTH} levels")

    # -- names --------------------------------------------------------------------

    def name_at(self, offset: int) -> str:
        cached = self.names.get(offset)
        if cached is not None:
            return cached
        if offset + 8 > len(self.chunk):
            raise EvtxFormat(f"a name offset {offset} points outside the chunk")
        _next, _hash, count = struct.unpack_from("<IHH", self.chunk, offset)
        raw = self.chunk[offset + 8:offset + 8 + 2 * count]
        name = raw.decode("utf-16-le", "replace")
        self.names[offset] = name
        return name

    def name_size(self, offset: int) -> int:
        count = struct.unpack_from("<H", self.chunk, offset + 6)[0]
        return 8 + 2 * count + 2

    def read_name(self, pos: int):
        """Name offset field at pos. Returns (name, new_pos)."""
        offset = struct.unpack_from("<I", self.chunk, pos)[0]
        pos += 4
        if offset == pos:  # the name is stored inline, right here
            pos += self.name_size(offset)
        return self.name_at(offset), pos

    # -- fragments --------------------------------------------------------------------

    def parse_at(self, pos: int, end: int, subs):
        """Parse tokens from pos to end; returns the root Element (or a list of nodes)."""
        nodes = []
        while pos < end:
            token = self.chunk[pos]
            if token == 0x00:  # EOF
                pos += 1
                break
            if token == 0x0F:  # fragment header
                pos += 4
                continue
            if token == 0x0C:  # template instance
                node, pos = self.parse_template_instance(pos)
                nodes.append(node)
                continue
            if token in (0x01, 0x41):
                node, pos = self.parse_element(pos, subs)
                nodes.append(node)
                continue
            try:
                value, pos = self.parse_value_token(pos, subs)
            except UnknownToken:
                self.skipped += 1  # no size to skip by at this level: keep what came before
                break
            nodes.append(value)
        if len(nodes) == 1:
            return nodes[0]
        return nodes

    def parse_template_instance(self, pos: int):
        self.enter()
        try:
            return self._template_instance(pos)
        finally:
            self.depth -= 1

    def _template_instance(self, pos: int):
        _token, _version, _template_id, def_offset = struct.unpack_from("<BBII", self.chunk, pos)
        pos += 10
        if def_offset == pos:
            # Resident definition: next(4) guid(16) size(4) then the binary xml.
            _next, size = struct.unpack_from("<I16xI", self.chunk, pos)
            xml_start = pos + 24
            pos = xml_start + size
            template = self.template_at(def_offset)
        else:
            template = self.template_at(def_offset)
        # Substitution array.
        count = struct.unpack_from("<I", self.chunk, pos)[0]
        pos += 4
        if count > 4096:
            raise EvtxFormat(f"a substitution array claims {count} values")
        specs = []
        for i in range(count):
            size, kind, _pad = struct.unpack_from("<HBB", self.chunk, pos)
            specs.append((size, kind))
            pos += 4
        resolved = []
        for size, kind in specs:
            raw = self.chunk[pos:pos + size]
            if kind == 0x21:
                resolved.append(self.parse_at(pos, pos + size, None) if size else None)
            else:
                resolved.append(decode_value(kind, raw))
            pos += size
        node = self.instantiate(template, resolved)
        return node, pos

    def template_at(self, offset: int):
        cached = self.templates.get(offset)
        if cached is None:
            if offset + 24 > len(self.chunk):
                raise EvtxFormat(f"a template offset {offset} points outside the chunk")
            _next, size = struct.unpack_from("<I16xI", self.chunk, offset)
            start = offset + 24
            before = self.skipped
            tree = self.parse_at(start, start + size, "template")
            cached = self.templates[offset] = (tree, self.skipped - before)
        if cached[1]:
            self.skipped += 1  # every record built from a damaged template is partial
        return cached[0]

    def parse_element(self, pos: int, subs):
        self.enter()
        try:
            return self._element(pos, subs)
        finally:
            self.depth -= 1

    def _element(self, pos: int, subs):
        token = self.chunk[pos]
        has_attrs = bool(token & 0x40)
        pos += 1
        pos += 2  # dependency identifier
        size = struct.unpack_from("<I", self.chunk, pos)[0]
        pos += 4
        end = pos + size
        name, pos = self.read_name(pos)
        element = Element(name)
        if has_attrs:
            attr_size = struct.unpack_from("<I", self.chunk, pos)[0]
            pos += 4
            attr_end = pos + attr_size
            while pos < attr_end:
                atoken = self.chunk[pos]
                if atoken not in (0x06, 0x46):
                    raise EvtxFormat(f"expected an attribute token at {pos}, found 0x{atoken:02x}")
                pos += 1
                aname, pos = self.read_name(pos)
                try:
                    value, pos = self.parse_value_token(pos, subs)
                except UnknownToken:
                    self.skipped += 1  # the attribute list's size says where it ends
                    break
                element.attrs[aname] = value
            pos = attr_end
        closer = self.chunk[pos]
        pos += 1
        if closer == 0x03:  # empty element
            return element, pos
        if closer != 0x02:
            raise EvtxFormat(f"expected the end of a start tag at {pos - 1}, found 0x{closer:02x}")
        while pos < len(self.chunk):
            t = self.chunk[pos]
            if t == 0x04:
                pos += 1
                break
            if t in (0x01, 0x41):
                child, pos = self.parse_element(pos, subs)
                element.children.append(child)
            elif t == 0x0C:
                child, pos = self.parse_template_instance(pos)
                element.children.append(child)
            elif t == 0x00:
                pos += 1
                break
            else:
                try:
                    value, pos = self.parse_value_token(pos, subs)
                except UnknownToken:
                    if not pos < end <= len(self.chunk):
                        raise
                    self.skipped += 1  # the element's size says where it ends: keep what came before
                    return element, end
                element.children.append(value)
        return element, pos

    def parse_value_token(self, pos: int, subs):
        token = self.chunk[pos]
        base = token & 0x3F
        pos += 1
        if base == 0x05:  # value
            kind = self.chunk[pos]
            pos += 1
            if kind == 0x01:
                count = struct.unpack_from("<H", self.chunk, pos)[0]
                pos += 2
                text = self.chunk[pos:pos + 2 * count].decode("utf-16-le", "replace")
                return text, pos + 2 * count
            if kind == 0x02:
                count = struct.unpack_from("<H", self.chunk, pos)[0]
                pos += 2
                return self.chunk[pos:pos + count].decode("latin-1"), pos + count
            size = {0x03: 1, 0x04: 1, 0x05: 2, 0x06: 2, 0x07: 4, 0x08: 4, 0x09: 8, 0x0A: 8, 0x0B: 4, 0x0C: 8,
                    0x0D: 4, 0x0F: 16, 0x11: 8, 0x12: 16, 0x14: 4, 0x15: 8}.get(kind)
            if size is None:
                raise UnknownToken(f"an inline value of type 0x{kind:02x} has no known size")
            return decode_value(kind, self.chunk[pos:pos + size]), pos + size
        if base in (0x0D, 0x0E):  # substitution
            index, kind = struct.unpack_from("<HB", self.chunk, pos)
            return Substitution(index, kind, base == 0x0E), pos + 3
        if base == 0x07:  # CDATA
            count = struct.unpack_from("<H", self.chunk, pos)[0]
            pos += 2
            return self.chunk[pos:pos + 2 * count].decode("utf-16-le", "replace"), pos + 2 * count
        if base == 0x08:  # character reference
            value = struct.unpack_from("<H", self.chunk, pos)[0]
            return chr(value), pos + 2
        if base == 0x09:  # entity reference
            name, pos = self.read_name(pos)
            return {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}.get(name, f"&{name};"), pos
        if base == 0x0A:  # processing instruction target
            name, pos = self.read_name(pos)
            return f"<?{name}", pos
        if base == 0x0B:  # processing instruction data
            count = struct.unpack_from("<H", self.chunk, pos)[0]
            pos += 2
            return self.chunk[pos:pos + 2 * count].decode("utf-16-le", "replace") + "?>", pos + 2 * count
        raise UnknownToken(f"unknown binary XML token 0x{token:02x} at offset {pos - 1}")

    # -- template instantiation ------------------------------------------------------

    def instantiate(self, template, values):
        def fill(node):
            if isinstance(node, Substitution):
                if node.index >= len(values):
                    return None
                return values[node.index]
            if isinstance(node, Element):
                out = Element(node.name)
                for k, v in node.attrs.items():
                    filled = fill(v)
                    if isinstance(v, Substitution) and v.optional and filled is None:
                        continue
                    out.attrs[k] = filled
                for c in node.children:
                    filled = fill(c)
                    if isinstance(c, Substitution) and c.optional and filled is None:
                        continue
                    if isinstance(filled, list) and filled and all(isinstance(x, Element) for x in filled):
                        out.children.extend(filled)
                    else:
                        out.children.append(filled)
                return out
            if isinstance(node, list):
                return [fill(n) for n in node]
            return node
        return fill(template)


# =============================================================================
# File structure
# =============================================================================

def parse_file_header(data: bytes) -> dict:
    if len(data) < 4096 or data[:8] != b"ElfFile\x00":
        raise EvtxFormat("it doesn't start with the ElfFile signature")
    first_chunk, last_chunk, next_record, header_size, minor, major, block_size, chunk_count = \
        struct.unpack_from("<QQQIHHHH", data, 8)
    flags, checksum = struct.unpack_from("<II", data, 120)
    return {"first_chunk": first_chunk, "last_chunk": last_chunk, "next_record_id": next_record,
            "version": f"{major}.{minor}", "chunk_count": chunk_count, "block_size": block_size,
            "dirty": bool(flags & 1), "full": bool(flags & 2),
            "checksum_ok": (zlib.crc32(data[:120]) & 0xFFFFFFFF) == checksum}


def as_stream(src):
    return io.BytesIO(src) if isinstance(src, (bytes, bytearray, memoryview)) else src


def iter_chunks(f, damaged=False):
    """(index, chunk) for every 64 KB chunk after the file header, read one at a time from f.
    damaged=True also yields chunks whose signature is gone (for record recovery)."""
    index = 0
    while True:
        chunk = f.read(CHUNK_SIZE)
        if len(chunk) < 512:
            return
        if damaged or chunk[:8] == b"ElfChnk\x00":
            yield index, chunk
        index += 1


def chunk_header(chunk: bytes) -> dict:
    first_num, last_num, first_id, last_id, header_size, last_offset, free_offset, records_crc = \
        struct.unpack_from("<QQQQIIII", chunk, 8)
    header_crc = struct.unpack_from("<I", chunk, 124)[0]
    calc_header = zlib.crc32(chunk[:120] + chunk[128:512]) & 0xFFFFFFFF
    calc_records = zlib.crc32(chunk[512:free_offset]) & 0xFFFFFFFF if 512 <= free_offset <= len(chunk) else None
    return {"first_record_number": first_num, "last_record_number": last_num, "first_record_id": first_id,
            "last_record_id": last_id, "last_record_offset": last_offset, "free_space_offset": free_offset,
            "header_checksum_ok": calc_header == header_crc, "records_checksum_ok": calc_records == records_crc}


RECORD_MAGIC = b"\x2a\x2a\x00\x00"


def record_at(chunk: bytes, pos: int, careful: bool):
    """(size, record_id, written) if an event record starts at pos, else None. careful also
    wants the size copy at the record's end and the binary XML fragment header, which is
    how records are told apart from leftover bytes past the chunk's last known record."""
    if pos + 28 > len(chunk):
        return None
    magic, size = struct.unpack_from("<II", chunk, pos)
    if magic != 0x00002A2A or size < 28 or pos + size > len(chunk):
        return None
    if careful and (struct.unpack_from("<I", chunk, pos + size - 4)[0] != size
                    or chunk[pos + 24:pos + 28] != b"\x0f\x01\x01\x00"):
        return None
    record_id, written = struct.unpack_from("<QQ", chunk, pos + 8)
    return size, record_id, written


def iter_raw_records(chunk: bytes, header: dict, dirty: bool = False, recover: bool = False):
    """(pos, size, record_id, written, recovered) for each record in a chunk.

    Live records run from offset 512 up to the header's free space offset. In a file
    marked dirty the chunk header may be stale, so whole records past that offset
    still count. recover=True then scans the rest of the chunk (its slack space) for
    the signatures of older, overwritten or deleted records; those come back with
    recovered=True."""
    pos = 512
    free = header["free_space_offset"]
    end = free if 512 <= free <= len(chunk) else len(chunk)
    if chunk[:8] == b"ElfChnk\x00":
        while pos < end:
            hit = record_at(chunk, pos, False)
            if not hit:
                break
            yield pos, hit[0], hit[1], hit[2], False
            pos += hit[0]
        while dirty:
            hit = record_at(chunk, pos, True)
            if not hit:
                break
            yield pos, hit[0], hit[1], hit[2], False
            pos += hit[0]
    if not recover:
        return
    while True:
        pos = chunk.find(RECORD_MAGIC, pos)
        if pos < 0:
            return
        hit = record_at(chunk, pos, True)
        if hit:
            yield pos, hit[0], hit[1], hit[2], True
            pos += hit[0]
        else:
            pos += 1


# =============================================================================
# Records
# =============================================================================

def plain(value):
    if isinstance(value, Element):
        return element_to_plain(value)
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def element_to_plain(el: Element):
    """An element with only text becomes its text; otherwise a map of children."""
    kids = [c for c in el.children if isinstance(c, Element)]
    if not kids and not el.attrs:
        return el.text()
    out = {}
    for k, v in el.attrs.items():
        out["@" + k] = plain(v)
    text = el.text()
    if text:
        out["#text"] = text
    for kid in kids:
        value = element_to_plain(kid)
        if kid.name == "Data" and kid.attrs.get("Name") is not None:
            out[str(kid.attrs["Name"])] = value["#text"] if isinstance(value, dict) and set(value) <= {"#text", "@Name"} else value
            if isinstance(out[str(kid.attrs["Name"])], dict) and "@Name" in out[str(kid.attrs["Name"])]:
                del out[str(kid.attrs["Name"])]["@Name"]
            continue
        if kid.name in out:
            if not isinstance(out[kid.name], list):
                out[kid.name] = [out[kid.name]]
            out[kid.name].append(value)
        else:
            out[kid.name] = value
    return out


def data_value(kid: Element):
    """The value of a <Data> element, keeping numbers, booleans and lists typed."""
    kids = [c for c in kid.children if isinstance(c, Element)]
    if kids:
        value = element_to_plain(kid)
        if isinstance(value, dict):
            value.pop("@Name", None)
        return value
    values = [c for c in kid.children if c is not None]
    if not values:
        return None
    if len(values) == 1 and not isinstance(values[0], str):
        return values[0]
    return kid.text()


def system_fields(event: Element) -> dict:
    system = event.find("System") if event.name == "Event" else None
    out: dict = {}
    if system is None:
        return out

    def text_of(name):
        el = system.find(name)
        return el.text() if el is not None else None

    def attr_of(name, attr):
        el = system.find(name)
        return el.attrs.get(attr) if el is not None else None

    provider = system.find("Provider")
    out["ProviderName"] = provider.attrs.get("Name") if provider is not None else None
    out["ProviderGuid"] = provider.attrs.get("Guid") if provider is not None else None
    event_id = text_of("EventID")
    out["EventID"] = logs.to_int(event_id) if event_id not in (None, "") else None
    out["Qualifiers"] = logs.to_int(attr_of("EventID", "Qualifiers"))
    for name in ("Version", "Level", "Task", "Opcode"):
        out[name] = logs.to_int(text_of(name))
    keywords = text_of("Keywords")
    out["Keywords"] = keywords
    out["TimeCreated"] = attr_of("TimeCreated", "SystemTime")
    out["EventRecordID"] = logs.to_int(text_of("EventRecordID"))
    out["ActivityID"] = attr_of("Correlation", "ActivityID")
    out["ProcessID"] = logs.to_int(attr_of("Execution", "ProcessID"))
    out["ThreadID"] = logs.to_int(attr_of("Execution", "ThreadID"))
    out["Channel"] = text_of("Channel")
    out["Computer"] = text_of("Computer")
    out["UserID"] = attr_of("Security", "UserID")
    return out


def keyword_names(keywords) -> list:
    if not isinstance(keywords, str) or not keywords.startswith("0x"):
        return []
    try:
        value = int(keywords, 16)
    except ValueError:
        return []
    names = []
    for mask, name in KEYWORD_NAMES.items():
        if value & mask == mask and name not in ("None",):
            names.append(name)
    return names


def build_record(root, record_id: int, written: int, chunk_index: int, want_xml: bool) -> dict:
    if isinstance(root, list):
        root = next((n for n in root if isinstance(n, Element)), None)
    if not isinstance(root, Element):
        raise EvtxFormat(f"record {record_id} has no XML element")
    fields = system_fields(root)
    xml_text = root.to_xml()
    event_data = {}
    data_el = root.find("EventData")
    if data_el is not None:
        anonymous = []
        for kid in data_el.children:
            if isinstance(kid, Element) and kid.name == "Data":
                name = kid.attrs.get("Name")
                value = data_value(kid)
                if name is None:
                    anonymous.append(value)
                else:
                    event_data[str(name)] = value
            elif isinstance(kid, Element):
                event_data[kid.name] = plain(kid)
        if anonymous:
            event_data["Data"] = anonymous if len(anonymous) > 1 else anonymous[0]
    user_data = root.find("UserData")
    if user_data is not None:
        for kid in user_data.children:
            if isinstance(kid, Element):
                value = plain(kid)
                if isinstance(value, dict):
                    event_data.update({k: v for k, v in value.items() if not k.startswith("@")})
                else:
                    event_data[kid.name] = value
    obj = dict(fields)
    obj["EventData"] = event_data
    obj["Keywords"] = keyword_names(fields.get("Keywords")) or fields.get("Keywords")
    obj["KeywordsDisplayNames"] = keyword_names(fields.get("Keywords"))
    obj["Message"] = None
    record = logs.windows_record(obj, xml_text)
    describe(record, fields.get("Channel"))
    record["source"] = "evtx"
    # Exported logs (wevtutil epl, Save As) renumber the record headers from 1, but each
    # record's own System/EventRecordID keeps the number Windows gave it; Event Viewer and
    # wevtutil show that one, so it wins. record_number is the position in this file.
    own = fields.get("EventRecordID")
    record["record_id"] = own if isinstance(own, int) and not isinstance(own, bool) and own > 0 else record_id
    record["record_number"] = record_id
    record["chunk"] = chunk_index
    record["written"] = filetime_stamp(written)
    record["level"] = fields.get("Level")
    record["level_name"] = LEVELS.get(fields.get("Level") or 0)
    record["channel"] = fields.get("Channel")
    record["task"] = fields.get("Task")
    record["opcode"] = fields.get("Opcode")
    record["task_name"] = SECURITY_TASKS.get(record["task"]) if record["provider"] == SECURITY_PROVIDER else None
    record["opcode_name"] = OPCODES.get(record["opcode"])
    rendering = root.find("RenderingInfo")  # forwarded and rendered events carry the names Windows showed
    if rendering is not None:
        for name, key in (("Task", "task_name"), ("Opcode", "opcode_name"), ("Level", "level_name")):
            el = rendering.find(name)
            if el is not None and el.text():
                record[key] = el.text()
        message = rendering.find("Message")
        if message is not None and message.text():
            record["message"] = message.text()
    record["keywords"] = obj["Keywords"]
    record["keywords_hex"] = fields.get("Keywords")
    record["provider_guid"] = fields.get("ProviderGuid")
    record["process_id"] = fields.get("ProcessID")
    record["thread_id"] = fields.get("ThreadID")
    record["user_sid"] = fields.get("UserID")
    record["activity_id"] = fields.get("ActivityID")
    record["event_data"] = event_data
    if record.get("time") is None and fields.get("TimeCreated"):
        ts, when = logs.parse_time(fields["TimeCreated"])
        record["time"], record["when"] = ts, when
    if want_xml:
        record["xml"] = xml_text
    record["raw"] = xml_text
    if not record.get("message") or record["message"] == xml_text:
        record["message"] = record.get("description") or f"Event {record.get('event_id')}"
    return record


# What a damaged record can raise while it is decoded; it then becomes a "broken" placeholder.
DAMAGE = (EvtxFormat, struct.error, IndexError, ValueError, UnicodeError, OverflowError, TypeError, KeyError)


def iter_records(src, want_xml: bool = False, strict: bool = False, recover: bool = False):
    """Yield record maps from EVTX bytes or a binary file object, read a chunk at a time.
    Broken records become placeholders (or raise when strict); recover adds records
    carved from chunk slack space, marked "recovered": true."""
    f = as_stream(src)
    header = parse_file_header(f.read(4096))
    for index, chunk in iter_chunks(f, recover):
        ch = chunk_header(chunk)
        parser = ChunkParser(chunk)
        for pos, size, record_id, written, recovered in iter_raw_records(chunk, ch, header["dirty"], recover):
            parser.skipped = 0
            try:
                root = parser.parse_at(pos + 24, pos + size - 4, None)
                record = build_record(root, record_id, written, index, want_xml)
            except DAMAGE as err:
                if recovered:
                    continue  # leftover bytes that only looked like a record
                if strict:
                    raise EvtxFormat(f"record {record_id} in chunk {index} is broken: {err}")
                yield {"time": filetime_stamp(written), "when": None, "format": "windows", "source": "evtx",
                       "record_id": record_id, "chunk": index, "broken": True,
                       "message": f"Record {record_id} could not be decoded: {err}", "raw": "", "event_id": None,
                       "EventID": None, "action": None, "description": None}
                continue
            if parser.skipped:
                record["partial"] = True  # unknown binary XML tokens were skipped
            if recovered:
                record["recovered"] = True
            yield record


def file_info(src) -> dict:
    f = as_stream(src)
    header = parse_file_header(f.read(4096))
    chunks = []
    for index, chunk in iter_chunks(f):
        ch = chunk_header(chunk)
        ch["index"] = index
        ch["records"] = sum(1 for _ in iter_raw_records(chunk, ch, header["dirty"]))
        chunks.append(ch)
    header["chunks"] = chunks
    header["records"] = sum(c["records"] for c in chunks)
    header["checksums_ok"] = header["checksum_ok"] and all(c["header_checksum_ok"] and c["records_checksum_ok"] for c in chunks)
    return header


def is_evtx(data: bytes) -> bool:
    return data[:8] == b"ElfFile\x00"


# =============================================================================
# The module
# =============================================================================

def iter_path(line, path, fn="evtx.read"):
    """iter_path(line, path, fn) -> generator of records
    Records from an .evtx file (also .gz or one-file .zip), streamed a chunk at a time,
    for other libraries (logs.read delegates here). Fails with a friendly TB error if
    the file isn't an EVTX log."""
    with _magic.open_stream(line, path, fn) as f:
        if not is_evtx(f.peek(8)[:8]):
            fail(line, f"'{path}' isn't an EVTX log: it doesn't start with the EVTX signature (ElfFile).", path=path)
        yield from records_of(line, f, path, options_of(line, None, fn))


def options_of(line, options, fn):
    opts = {"limit": None, "xml": False, "strict": False, "recover": False}
    if options is None:
        return opts
    check_type(line, options, "map", fn, "options")
    if options.get("limit") is not None:
        opts["limit"] = check_type(line, options["limit"], "whole", fn, "the limit")
    for name in ("xml", "strict", "recover"):
        opts[name] = bool(options.get(name, False))
    return opts


def records_of(line, src, path, opts):
    """Records from EVTX bytes or a file object, one at a time, with friendly TB errors."""
    try:
        records = iter_records(src, opts["xml"], opts["strict"], opts["recover"])
        for n, record in enumerate(records):
            if opts["limit"] is not None and n >= opts["limit"]:
                return
            yield record
    except (EvtxFormat, struct.error) as err:
        what = f"'{path}'" if path else "that data"
        fail(line, f"{what} isn't an EVTX log I can read: {err}.", path=path)


def info_of(line, path, fn):
    with _magic.open_stream(line, path, fn) as f:
        try:
            return file_info(f)
        except (EvtxFormat, struct.error) as err:
            fail(line, f"'{path}' isn't an EVTX log I can read: {err}.", path=path)


def records_or_fail(line, src, path, opts, fn):
    out = []
    for record in records_of(line, src, path, opts):
        if len(out) >= MAX_RECORDS:
            break
        out.append(record)
    return out


def summary_of(records: list, info: dict | None) -> dict:
    by_id, by_action, by_channel, by_computer, by_provider = {}, {}, {}, {}, {}
    failures, users, ips = 0, {}, {}
    broken = 0
    for r in records:
        if r.get("broken"):
            broken += 1
            continue
        eid = r.get("event_id")
        by_id[eid] = by_id.get(eid, 0) + 1
        if r.get("action"):
            by_action[r["action"]] = by_action.get(r["action"], 0) + 1
        for key, bucket in (("channel", by_channel), ("computer", by_computer), ("provider", by_provider)):
            if r.get(key):
                bucket[r[key]] = bucket.get(r[key], 0) + 1
        if r.get("success") is False:
            failures += 1
        if r.get("user"):
            users[r["user"]] = users.get(r["user"], 0) + 1
        if r.get("ip"):
            ips[r["ip"]] = ips.get(r["ip"], 0) + 1
    times = [r["time"] for r in records if r.get("time")]
    whens = [r for r in records if r.get("time")]
    whens.sort(key=lambda r: r["time"])

    def top(d, n=10):
        return dict(sorted(d.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n])

    out = {"records": len(records), "broken": broken, "first": min(times) if times else None,
           "last": max(times) if times else None,
           "first_when": whens[0]["when"] if whens else None, "last_when": whens[-1]["when"] if whens else None,
           "event_ids": top(by_id, 20), "actions": top(by_action, 20), "channels": top(by_channel),
           "computers": top(by_computer), "providers": top(by_provider), "failures": failures,
           "users": top(users), "ips": top(ips)}
    if info:
        out["chunks"] = len(info["chunks"])
        out["version"] = info["version"]
        out["dirty"] = info["dirty"]
        out["checksums_ok"] = info["checksums_ok"]
        out["next_record_id"] = info["next_record_id"]
    return out


def load(api):
    m = Module(
        "evtx",
        "Read binary Windows event logs (.evtx, the files in C:\\Windows\\System32\\winevt\\Logs) straight from "
        "the file, with no Windows needed. Each event becomes the same kind of record the logs library makes for "
        "Windows exports: EventID, time, computer, channel, provider, the EventData fields by name "
        "(TargetUserName, IpAddress, CommandLine...), plus action/description/user/ip/success worked out the same "
        "way, so the log-triage rules and Sigma rules run unchanged. Parses the binary XML (chunks, templates, "
        "substitutions) itself. Times are read as UTC and shown in local time like logs does.",
    )

    @m.fn("read", 1, 2, doc=(
        "read(path, options?) -> list\n"
        "Every event in an .evtx file, as a list of records.\n"
        "\n"
        "Options:\n"
        "- limit: stop after this many events.\n"
        "- xml: true adds the event's XML as \"xml\".\n"
        "- strict: true fails on a broken record.\n"
        "  Without it, a broken record is kept as a placeholder with \"broken\": true.\n"
        "- recover: true also carves old or deleted records out of the chunks' slack space.\n"
        "  They come back with \"recovered\": true; Windows itself no longer shows them.\n"
        "\n"
        "Fields of each record:\n"
        "- \"time\", \"when\"\n"
        "- \"event_id\", \"action\", \"description\"\n"
        "- \"user\", \"ip\", \"success\"\n"
        "- \"computer\", \"channel\", \"provider\", \"level_name\", \"record_id\" (Windows' EventRecordID), \"record_number\" (position in the file)\n"
        "- \"task_name\", \"opcode_name\": when known (Security task categories, standard opcodes).\n"
        "- \"event_data\": a map of the EventData fields.\n"
        "  Every EventData field is also its own key.\n"
        "- \"partial\": true when parts of the event used binary XML this reader doesn't know.\n"
        "- \"raw\": the XML.\n"
        "\n"
        "A .gz or one-file .zip of an .evtx file is unpacked as it is read."))
    def read(line, path, options=None):
        opts = options_of(line, options, "evtx.read")
        with _magic.open_stream(line, path, "evtx.read") as f:
            return records_or_fail(line, f, path, opts, "evtx.read")

    @m.fn("parse", 1, 2, doc="parse(data, options?) -> list\nLike evtx.read() for EVTX bytes already in memory.")
    def parse(line, data, options=None):
        raw = _magic.to_bytes(line, data, "evtx.parse")
        opts = options_of(line, options, "evtx.parse")
        return records_or_fail(line, raw, None, opts, "evtx.parse")

    @m.fn("each", 2, 3, doc="each(path, fn, options?) -> number\nCall fn(record) for every event, one at a time, "
                            "streaming the file from disk a chunk at a time, so logs of any size use little memory. "
                            "Takes the same options as read(). Return false from fn to stop early. "
                            "Gives back how many events fn saw.")
    def each(line, path, fn, options=None):
        check_type(line, fn, "function", "evtx.each", "fn")
        opts = options_of(line, options, "evtx.each")
        count = 0
        with _magic.open_stream(line, path, "evtx.each") as f:
            for record in records_of(line, f, path, opts):
                count += 1
                if api.call(fn, [record], line) is False:
                    break
        return count

    @m.fn("summary", 1, doc=(
        "summary(path_or_records) -> map\n"
        "An overview of an event log. Give it a path or the records from evtx.read().\n"
        "\n"
        "Fields:\n"
        "- \"records\", \"first_when\", \"last_when\"\n"
        "- \"event_ids\": a map of id -> count.\n"
        "- \"actions\", \"channels\", \"computers\", \"providers\"\n"
        "- \"failures\", \"users\", \"ips\"\n"
        "- \"chunks\", \"version\", \"dirty\", \"checksums_ok\""))
    def summary(line, target):
        if isinstance(target, list):
            return summary_of(target, None)
        info = info_of(line, target, "evtx.summary")
        with _magic.open_stream(line, target, "evtx.summary") as f:
            records = records_or_fail(line, f, target, options_of(line, None, "evtx.summary"), "evtx.summary")
        return summary_of(records, info)

    @m.fn("info", 1, doc=(
        "info(path) -> map\n"
        "The file's own bookkeeping.\n"
        "\n"
        "Fields:\n"
        "- \"version\", \"chunk_count\", \"next_record_id\"\n"
        "- \"dirty\": true if the file wasn't closed cleanly.\n"
        "- \"full\", \"checksum_ok\", \"records\", \"checksums_ok\"\n"
        "- \"chunks\": a list of maps, one per chunk, with:\n"
        "  - \"index\", \"first_record_id\", \"last_record_id\", \"records\"\n"
        "  - \"header_checksum_ok\", \"records_checksum_ok\""))
    def info(line, path):
        return info_of(line, path, "evtx.info")

    @m.fn("is_evtx", 1, doc="is_evtx(path) -> bool\nTrue if the file starts with the EVTX signature (ElfFile).")
    def is_evtx_fn(line, path):
        return is_evtx(_magic.read_head(line, path, "evtx.is_evtx", 8))

    @m.fn("to_json", 1, doc="to_json(record) -> text\nOne record as a JSON line (the same shape "
                            "Get-WinEvent | ConvertTo-Json gives), handy for feeding other tools.")
    def to_json(line, record):
        check_type(line, record, "map", "evtx.to_json", "record")
        slim = {k: v for k, v in record.items() if k not in ("raw", "xml")}
        return _json.dumps(slim, ensure_ascii=False, default=str)

    m.value("levels", dict(LEVELS), doc="Level numbers to names: 1 Critical, 2 Error, 3 Warning, 4 Information, 5 Verbose.")
    return m
