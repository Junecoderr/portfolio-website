"""macho: read macOS and iOS executables (Mach-O, including universal binaries) without running them."""

from __future__ import annotations

import struct

from tblib import _magic
from tblib._api import Module, fail
from tblib.pe import pkcs7_info

MAX_FILE = 512 * 1024 * 1024

MAGICS = {b"\xce\xfa\xed\xfe": (32, "<"), b"\xcf\xfa\xed\xfe": (64, "<"),
          b"\xfe\xed\xfa\xce": (32, ">"), b"\xfe\xed\xfa\xcf": (64, ">")}
FAT_MAGICS = {b"\xca\xfe\xba\xbe": 20, b"\xca\xfe\xba\xbf": 32}

CPUS = {
    7: ("x86", "Intel x86"), 0x01000007: ("x64", "x86-64"), 12: ("arm", "ARM"), 0x0100000C: ("arm64", "ARM64"),
    0x0200000C: ("arm64_32", "ARM64_32"), 18: ("powerpc", "PowerPC"), 0x01000012: ("powerpc64", "PowerPC 64"),
}
FILE_TYPES = {1: "object", 2: "executable", 3: "fvmlib", 4: "core", 5: "preload", 6: "dylib", 7: "dylinker",
              8: "bundle", 9: "dylib_stub", 10: "dsym", 11: "kext_bundle", 12: "fileset"}
HEADER_FLAGS = [
    (0x1, "noundefs"), (0x4, "dyldlink"), (0x80, "twolevel"), (0x8000, "weak_defines"), (0x10000, "binds_to_weak"),
    (0x20000, "allow_stack_execution"), (0x40000, "root_safe"), (0x80000, "setuid_safe"),
    (0x100000, "no_reexported_dylibs"), (0x200000, "pie"), (0x800000, "has_tlv_descriptors"),
    (0x1000000, "no_heap_execution"), (0x2000000, "app_extension_safe"), (0x80000000, "dylib_in_cache"),
]
LOAD_COMMANDS = {
    0x1: "segment", 0x2: "symtab", 0x4: "thread", 0x5: "unixthread", 0xB: "dysymtab", 0xC: "load_dylib",
    0xD: "id_dylib", 0xE: "load_dylinker", 0xF: "id_dylinker", 0x19: "segment_64", 0x1B: "uuid",
    0x1D: "code_signature", 0x1E: "segment_split_info", 0x20: "lazy_load_dylib", 0x21: "encryption_info",
    0x22: "dyld_info", 0x24: "version_min_macosx", 0x25: "version_min_iphoneos", 0x26: "function_starts",
    0x27: "dyld_environment", 0x29: "data_in_code", 0x2A: "source_version", 0x2B: "dylib_code_sign_drs",
    0x2C: "encryption_info_64", 0x2D: "linker_option", 0x2F: "version_min_tvos", 0x30: "version_min_watchos",
    0x31: "note", 0x32: "build_version", 0x80000018: "load_weak_dylib", 0x8000001C: "rpath",
    0x8000001F: "reexport_dylib", 0x80000022: "dyld_info_only", 0x80000023: "load_upward_dylib",
    0x80000028: "main", 0x80000033: "dyld_exports_trie", 0x80000034: "dyld_chained_fixups",
    0x80000035: "fileset_entry",
}
DYLIB_KINDS = {0xC: "load", 0x80000018: "weak", 0x8000001F: "reexport", 0x20: "lazy", 0x80000023: "upward"}
PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 5: "bridgeos", 6: "maccatalyst", 7: "ios_simulator",
             8: "tvos_simulator", 9: "watchos_simulator", 10: "driverkit", 11: "visionos"}
VERSION_MIN = {0x24: "macos", 0x25: "ios", 0x2F: "tvos", 0x30: "watchos"}
HASH_TYPES = {1: "sha1", 2: "sha256", 3: "sha256_truncated", 4: "sha384"}

SUSPICIOUS_IMPORTS = {
    "injection": ["_task_for_pid", "_mach_vm_write", "_vm_write", "_thread_create_running", "_mach_inject",
                  "_thread_set_state"],
    "anti_debug": ["_ptrace"],
    "keylogging": ["_CGEventTapCreate", "_IOHIDManagerRegisterInputValueCallback", "_NSEvent_addGlobalMonitor"],
    "dynamic_loading": ["_dlopen", "_dlsym", "_NSCreateObjectFileImageFromMemory", "_NSLinkModule"],
    "execution": ["_system", "_execve", "_execv", "_popen", "_posix_spawn", "_fork"],
    "privilege": ["_AuthorizationExecuteWithPrivileges", "_setuid", "_seteuid"],
    "persistence": ["_SMLoginItemSetEnabled", "_LSSharedFileListInsertItemURL", "_SMJobBless"],
    "network": ["_socket", "_connect", "_getaddrinfo", "_gethostbyname", "_curl_easy_perform"],
}


class MachOFormat(Exception):
    pass


def flags_of(value: int, table) -> list:
    return [name for bit, name in table if value & bit]


def cstr(data: bytes, offset: int, limit: int = 1024) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset, offset + limit)
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("utf-8", "replace")


def version(v: int) -> str:
    """xxxx.yy.zz packed into 32 bits."""
    return f"{v >> 16}.{(v >> 8) & 0xFF}" + (f".{v & 0xFF}" if v & 0xFF else "")


def prot(p: int) -> str:
    return ("r" if p & 1 else "-") + ("w" if p & 2 else "-") + ("x" if p & 4 else "-")


def arch_of(cputype: int, subtype: int) -> tuple:
    arch, name = CPUS.get(cputype, (f"0x{cputype:x}", f"unknown CPU 0x{cputype:x}"))
    if cputype == 0x0100000C and subtype & 0xFF == 2:
        return "arm64e", "ARM64e (pointer authentication)"
    return arch, name


class MachO:
    """One thin (single-architecture) Mach-O image. `info()` gives the TB map."""

    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 28 or data[:4] not in MAGICS:
            raise MachOFormat("it doesn't start with a Mach-O magic number (feedface or feedfacf)")
        self.bits, self.e = MAGICS[data[:4]]
        e = self.e
        (_m, self.cputype, self.subtype, self.filetype, self.ncmds, self.sizeofcmds,
         self.flags) = struct.unpack_from(e + "IiiIIII", data, 0)
        self.header_size = 32 if self.bits == 64 else 28
        self.truncated = self.header_size + self.sizeofcmds > len(data)
        self.parse_commands()

    def parse_commands(self):
        d, e = self.data, self.e
        self.commands, self.segments, self.libraries, self.rpaths = [], [], [], []
        self.dylib_id = self.dylinker = self.uuid = self.entry_point = self.platform = None
        self.min_os = self.sdk = self.source_version = None
        self.symtab = self.code_signature_at = self.encryption = None
        off = self.header_size
        end = min(len(d), self.header_size + self.sizeofcmds)
        for _ in range(min(self.ncmds, 4096)):
            if off + 8 > end:
                self.truncated = True
                break
            cmd, size = struct.unpack_from(e + "II", d, off)
            if size < 8 or off + size > end:
                self.truncated = True
                break
            name = LOAD_COMMANDS.get(cmd, f"0x{cmd:x}")
            self.commands.append({"command": name, "size": size})
            body = d[off:off + size]
            try:
                self.load_command(cmd, off, body)
            except struct.error:
                self.truncated = True
            off += size

    def lc_str(self, body: bytes, at: int) -> str:
        (where,) = struct.unpack_from(self.e + "I", body, at)
        return cstr(body, where, 1024)

    def load_command(self, cmd: int, off: int, body: bytes):
        e = self.e
        if cmd in (0x1, 0x19):
            self.segments.append(self.segment(cmd == 0x19, body))
        elif cmd in DYLIB_KINDS:
            _ts, current, compat = struct.unpack_from(e + "III", body, 12)
            self.libraries.append({"name": self.lc_str(body, 8), "kind": DYLIB_KINDS[cmd],
                                   "current_version": version(current), "compatibility_version": version(compat)})
        elif cmd == 0xD:
            self.dylib_id = self.lc_str(body, 8)
        elif cmd == 0xE:
            self.dylinker = self.lc_str(body, 8)
        elif cmd == 0x8000001C:
            self.rpaths.append(self.lc_str(body, 8))
        elif cmd == 0x1B:
            u = body[8:24].hex().upper()
            self.uuid = f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"
        elif cmd == 0x80000028:
            self.entry_point = struct.unpack_from(e + "Q", body, 8)[0]
        elif cmd == 0x32:
            platform, minos, sdk = struct.unpack_from(e + "III", body, 8)
            self.platform, self.min_os, self.sdk = PLATFORMS.get(platform, str(platform)), version(minos), version(sdk)
        elif cmd in VERSION_MIN:
            minos, sdk = struct.unpack_from(e + "II", body, 8)
            self.platform, self.min_os, self.sdk = VERSION_MIN[cmd], version(minos), version(sdk)
        elif cmd == 0x2A:
            (v,) = struct.unpack_from(e + "Q", body, 8)
            parts = [v >> 40, (v >> 30) & 0x3FF, (v >> 20) & 0x3FF, (v >> 10) & 0x3FF, v & 0x3FF]
            self.source_version = ".".join(map(str, parts))
        elif cmd == 0x2:
            self.symtab = struct.unpack_from(e + "IIII", body, 8)
        elif cmd == 0x1D:
            self.code_signature_at = struct.unpack_from(e + "II", body, 8)
        elif cmd in (0x21, 0x2C):
            cryptoff, cryptsize, cryptid = struct.unpack_from(e + "III", body, 8)
            self.encryption = {"offset": cryptoff, "size": cryptsize, "encrypted": cryptid != 0}

    def segment(self, is64: bool, body: bytes) -> dict:
        e, d = self.e, self.data
        if is64:
            name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, _f = \
                struct.unpack_from(e + "16sQQQQiiII", body, 8)
            pos, sect_fmt, sect_size = 72, e + "16s16sQQIIIIIIII", 80
        else:
            name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, _f = \
                struct.unpack_from(e + "16sIIIIiiII", body, 8)
            pos, sect_fmt, sect_size = 56, e + "16s16sIIIIIIIII", 68
        sections = []
        for _ in range(min(nsects, 256)):
            if pos + sect_size > len(body):
                break
            sname, sseg, addr, size, offset, _al, _ro, _nr, sflags = struct.unpack_from(sect_fmt, body, pos)[:9]
            pos += sect_size
            zerofill = sflags & 0xFF in (0x1, 0xC, 0x12)
            chunk = b"" if zerofill or not offset else d[offset:offset + size]
            sections.append({"name": sname.rstrip(b"\x00").decode("latin-1"),
                             "segment": sseg.rstrip(b"\x00").decode("latin-1"), "address": addr, "size": size,
                             "offset": offset, "entropy": _magic.entropy(chunk) if chunk else 0.0,
                             "executable": bool(sflags & 0x80000400)})
        if fileoff + filesize > len(d):
            self.truncated = True
        return {"name": name.rstrip(b"\x00").decode("latin-1"), "address": vmaddr, "vm_size": vmsize,
                "offset": fileoff, "file_size": filesize, "max_protection": prot(maxprot), "protection": prot(initprot),
                "readable": bool(initprot & 1), "writable": bool(initprot & 2), "executable": bool(initprot & 4),
                "sections": sections}

    def symbols(self) -> list:
        if not self.symtab:
            return []
        d, e = self.data, self.e
        symoff, nsyms, stroff, strsize = self.symtab
        step, fmt = (16, e + "IBBHQ") if self.bits == 64 else (12, e + "IBBHI")
        out = []
        for i in range(min(nsyms, 200000)):
            o = symoff + i * step
            if o + step > len(d):
                self.truncated = True
                break
            strx, n_type, n_sect, _desc, value = struct.unpack_from(fmt, d, o)
            if n_type & 0xE0 or strx >= strsize:  # debugger (stab) entries
                continue
            name = cstr(d, stroff + strx, 1024)
            if name:
                out.append({"name": name, "external": bool(n_type & 0x01), "defined": n_type & 0x0E != 0,
                            "value": value})
        return out

    def code_signature(self):
        """What the embedded signature says (identifier, team, ad-hoc or not, certificates); never verified."""
        if not self.code_signature_at:
            return None
        d = self.data
        off, size = self.code_signature_at
        blob = d[off:off + size]
        out = {"identifier": None, "team_id": None, "adhoc": False, "hardened_runtime": False, "hash_type": None,
               "has_cms": False, "signer": None, "certificates": [], "entitlements": None, "verified": False,
               "problem": None}
        if len(blob) < 12 or blob[:4] != b"\xfa\xde\x0c\xc0":
            out["problem"] = "the code signature block is damaged or missing"
            return out
        (count,) = struct.unpack_from(">I", blob, 8)
        for i in range(min(count, 64)):
            if 12 + 8 * i + 8 > len(blob):
                break
            kind, at = struct.unpack_from(">II", blob, 12 + 8 * i)
            if at + 8 > len(blob):
                continue
            magic, length = struct.unpack_from(">II", blob, at)
            part = blob[at:at + length]
            if magic == 0xFADE0C02 and kind == 0 and len(part) >= 44:
                cd_version, cd_flags, _hash_off, ident_off = struct.unpack_from(">IIII", part, 8)
                out["identifier"] = cstr(part, ident_off, 512) or None
                out["adhoc"] = bool(cd_flags & 0x2)
                out["hardened_runtime"] = bool(cd_flags & 0x10000)
                out["hash_type"] = HASH_TYPES.get(part[37], str(part[37]))
                if cd_version >= 0x20200 and len(part) >= 52:
                    (team_off,) = struct.unpack_from(">I", part, 48)
                    out["team_id"] = cstr(part, team_off, 64) if team_off else None
            elif magic == 0xFADE7171:
                out["entitlements"] = part[8:].decode("utf-8", "replace")
            elif magic == 0xFADE0B01 and length > 8:
                out["has_cms"] = True
                cms = pkcs7_info(part[8:])
                out["signer"], out["certificates"] = cms["signer"], cms["certificates"]
                out["problem"] = cms["problem"]
        return out

    def info(self) -> dict:
        arch, machine = arch_of(self.cputype, self.subtype)
        flags = flags_of(self.flags, HEADER_FLAGS)
        syms = self.symbols()
        imports = sorted({s["name"] for s in syms if s["external"] and not s["defined"]})
        exports = sorted({s["name"] for s in syms if s["external"] and s["defined"]})
        signature = self.code_signature()
        sections = [s for seg in self.segments for s in seg["sections"]]
        entry_section = None
        if self.entry_point is not None:
            for s in sections:
                if s["offset"] <= self.entry_point < s["offset"] + max(s["size"], 1):
                    entry_section = f'{s["segment"]},{s["name"]}'
        encrypted = bool(self.encryption and self.encryption["encrypted"])
        names = set(imports)
        info = {
            "format": f"macho{self.bits}", "bits": self.bits, "endian": "little" if self.e == "<" else "big",
            "architecture": arch, "machine": machine, "type": FILE_TYPES.get(self.filetype, str(self.filetype)),
            "is_executable": self.filetype == 2, "is_dylib": self.filetype == 6, "is_bundle": self.filetype == 8,
            "flags": flags, "is_pie": "pie" in flags,
            "load_commands": self.commands, "segments": self.segments,
            "sections": [{**s, "name": f'{s["segment"]},{s["name"]}'} for s in sections],
            "libraries": self.libraries, "needed": [lib["name"] for lib in self.libraries],
            "dylib_id": self.dylib_id, "rpaths": self.rpaths, "dylinker": self.dylinker, "uuid": self.uuid,
            "entry_point": self.entry_point, "entry_hex": f"0x{self.entry_point:x}" if self.entry_point is not None else None,
            "entry_section": entry_section,
            "platform": self.platform, "min_os": self.min_os, "sdk": self.sdk, "source_version": self.source_version,
            "imports": imports, "exports": exports, "symbol_count": len(syms),
            "is_signed": signature is not None, "code_signature": signature, "is_encrypted": encrypted,
            "hardening": {
                "pie": "pie" in flags, "nx_stack": "allow_stack_execution" not in flags,
                "nx_heap": "no_heap_execution" in flags,
                "canary": bool(names & {"___stack_chk_fail", "___stack_chk_guard"}),
                "arc": any(n.startswith("_objc_release") for n in names),
                "signed": signature is not None, "hardened_runtime": bool(signature and signature["hardened_runtime"]),
                "encrypted": encrypted},
            "size": len(self.data), "entropy": _magic.entropy(self.data), "truncated": self.truncated,
        }
        info["indicators"] = indicators_for(info)
        return info


def indicators_for(info: dict) -> list:
    found = []

    def add(level, name, detail):
        found.append({"level": level, "name": name, "detail": detail})

    for seg in info["segments"]:
        if seg["writable"] and seg["executable"]:
            add("high", "writable_executable_segment", f"Segment {seg['name'] or '(unnamed)'} is both writable and executable.")
    for s in info["sections"]:
        if s["entropy"] >= 7.2 and s["size"] >= 512:
            add("medium", "high_entropy_section", f"Section {s['name']} has entropy {s['entropy']} (packed or encrypted data).")
        if "upx" in s["name"].lower():
            add("high", "packer_section_name", f"Section {s['name']} belongs to the UPX packer.")
    if "allow_stack_execution" in info["flags"]:
        add("medium", "executable_stack", "The header allows code to run from the stack.")
    if info["is_executable"] and not info["is_pie"]:
        add("low", "no_pie", "The program isn't position-independent (no ASLR).")
    if info["is_encrypted"]:
        add("medium", "encrypted", "Part of the program is encrypted (an App Store binary, or a protector).")
    if info.get("truncated"):
        add("medium", "truncated_file",
            "The file is shorter than its headers say: it was cut off (a partial download or a carved fragment).")
    sig = info["code_signature"]
    if sig is None:
        add("info", "unsigned", "No code signature: macOS on Apple silicon refuses to run it as is.")
    else:
        if sig["adhoc"] and not sig["has_cms"]:
            add("low", "adhoc_signature", "Signed ad hoc (no certificate): nobody vouches for who made it.")
        if sig["problem"]:
            add("medium", "damaged_signature", f"The code signature can't be read: {sig['problem']}.")
    for lib in info["libraries"] + [{"name": p} for p in info["rpaths"]]:
        if lib["name"].startswith(("/tmp/", "/private/tmp/", "/Users/Shared/", "/var/tmp/")):
            add("medium", "unusual_library_path", f"It loads code from {lib['name']}.")
    for group, names in SUSPICIOUS_IMPORTS.items():
        hits = [n for n in info["imports"] if n in names]
        if group in ("dynamic_loading", "execution", "network") and len(hits) < 2:
            continue
        if hits:
            level = {"injection": "high", "keylogging": "high", "anti_debug": "medium", "privilege": "medium"}.get(group, "low")
            add(level, f"imports_{group}", f"Imports {group.replace('_', ' ')} functions: {', '.join(sorted(hits)[:8])}.")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    found.sort(key=lambda i: order.get(i["level"], 9))
    return found


def parse_bytes(data: bytes) -> dict:
    head = data[:4]
    if head in FAT_MAGICS:
        return parse_fat(data)
    return MachO(data).info()


def parse_fat(data: bytes) -> dict:
    entry = FAT_MAGICS[data[:4]]
    if len(data) < 8:
        raise MachOFormat("the universal (fat) header is cut off")
    (count,) = struct.unpack_from(">I", data, 4)
    if count == 0 or count >= 0x30:
        raise MachOFormat("this is a Java class file or a damaged universal binary, not Mach-O")
    slices, first = [], None
    for i in range(count):
        o = 8 + entry * i
        if o + entry > len(data):
            raise MachOFormat("the universal (fat) header is cut off")
        if entry == 32:
            cputype, subtype, offset, size, align = struct.unpack_from(">iiQQI", data, o)
        else:
            cputype, subtype, offset, size, align = struct.unpack_from(">iiIII", data, o)
        arch, _ = arch_of(cputype, subtype)
        piece = {"architecture": arch, "offset": offset, "size": size, "align": 1 << min(align, 31),
                 "summary": None, "problem": None}
        try:
            sub = MachO(data[offset:offset + size]).info()
            piece["summary"] = describe(sub)
            piece["info"] = sub
            first = first or sub
        except (MachOFormat, struct.error, IndexError, ValueError) as err:
            piece["problem"] = str(err)
        slices.append(piece)
    if first is None:
        raise MachOFormat("none of the architectures inside the universal binary could be read")
    info = dict(first)
    info.update({"format": "macho-fat", "is_fat": True, "architectures": [s["architecture"] for s in slices],
                 "slices": slices, "size": len(data), "entropy": _magic.entropy(data),
                 "truncated": any(s["offset"] + s["size"] > len(data) for s in slices) or first["truncated"]})
    info["indicators"] = indicators_for(info)
    return info


def parse_or_fail(line, data: bytes, path=None) -> dict:
    try:
        info = parse_bytes(data)
    except MachOFormat as err:
        what = f"'{path}'" if path else "that data"
        kind = _magic.sniff(data)
        hint = "" if kind["type"] in ("macho", "macho-fat", "unknown") else f" It looks like {kind['description']}."
        fail(line, f"{what} isn't a Mach-O file: {err}.{hint}", path=path)
    except (struct.error, IndexError, ValueError) as err:
        what = f"'{path}'" if path else "that data"
        fail(line, f"{what} is a damaged or cut-off Mach-O file ({err}).", path=path)
    info.setdefault("is_fat", False)
    info.setdefault("architectures", [info["architecture"]])
    info.setdefault("slices", [])
    return info


def describe(info: dict) -> str:
    kind = info["type"].replace("_", " ")
    archs = info.get("architectures") or [info["architecture"]]
    first = f"{info['format']} {'+'.join(archs)} {kind}"
    if info["is_pie"]:
        first += " (PIE)"
    parts = [first, f"{len(info['segments'])} segments",
             f"needs {len(info['needed'])} librar{'y' if len(info['needed']) == 1 else 'ies'}",
             f"{len(info['imports'])} imports"]
    if info["exports"]:
        parts.append(f"{len(info['exports'])} exports")
    sig = info["code_signature"]
    parts.append("unsigned" if sig is None else "signed ad hoc" if sig["adhoc"] and not sig["has_cms"]
                 else f"signed by {sig['signer'] or sig['team_id'] or 'a certificate'}")
    high = sum(1 for i in info["indicators"] if i["level"] == "high")
    if high:
        parts.append(f"{high} high-risk indicator{'s' if high != 1 else ''}")
    return ", ".join(parts)


def load(api):
    m = Module(
        "macho",
        "Read macOS and iOS executables (Mach-O, including universal binaries) without running them: "
        "architectures, type, load commands, segments and sections with entropy and permissions, the libraries "
        "it needs, imported and exported symbols, the code signature (identifier, team, certificates, "
        "entitlements), a hardening report and suspicious indicators. Pure Python; works on bytes in the playground.",
    )

    @m.fn("parse", 1, doc=(
        "parse(path) -> map\n"
        "Everything macho knows about a file. Fails if the file isn't Mach-O.\n"
        "For a universal binary the fields describe its first architecture.\n"
        "\n"
        "Fields:\n"
        "- \"format\": macho32, macho64 or macho-fat.\n"
        "- \"architecture\": the CPU type, like \"arm64\" or \"x64\".\n"
        "- \"architectures\", \"slices\": every architecture in a universal binary.\n"
        "- \"type\": executable, dylib, bundle, object...\n"
        "- \"flags\", \"is_pie\", \"is_fat\", \"is_signed\", \"is_encrypted\"\n"
        "- \"load_commands\": a list of {command, size}.\n"
        "- \"segments\": a list of maps with name, address, offset, sizes, protection\n"
        "  and sections.\n"
        "- \"sections\": every section, named like \"__TEXT,__text\", with entropy.\n"
        "- \"libraries\": the dylibs it loads, each {name, kind, current_version}.\n"
        "- \"needed\", \"rpaths\", \"dylib_id\", \"dylinker\", \"uuid\"\n"
        "- \"entry_point\", \"entry_hex\", \"entry_section\"\n"
        "- \"platform\", \"min_os\", \"sdk\"\n"
        "- \"imports\", \"exports\": symbol names, like \"_printf\".\n"
        "- \"code_signature\": nil, or {identifier, team_id, adhoc, hardened_runtime,\n"
        "  hash_type, has_cms, signer, certificates, entitlements, verified, problem}.\n"
        "  Nothing is checked: verified is always false.\n"
        "- \"hardening\": pie, nx_stack, nx_heap, canary, arc, signed, hardened_runtime\n"
        "  and encrypted.\n"
        "- \"entropy\", \"size\", \"truncated\"\n"
        "- \"indicators\": a list of {level, name, detail} findings."))
    def parse(line, path):
        data = _magic.read_file(line, path, "macho.parse", MAX_FILE)
        info = parse_or_fail(line, data, path)
        info["path"] = path
        return info

    @m.fn("parse_bytes", 1, doc="parse_bytes(data) -> map\nLike macho.parse() but for bytes already in memory.")
    def parse_bytes_fn(line, data):
        return parse_or_fail(line, _magic.to_bytes(line, data, "macho.parse_bytes"))

    @m.fn("is_macho", 1, doc="is_macho(path) -> bool\nTrue if the file starts like a Mach-O or universal binary.")
    def is_macho(line, path):
        return _magic.sniff(_magic.read_head(line, path, "macho.is_macho", 64))["type"] in ("macho", "macho-fat")

    @m.fn("segments", 1, doc="segments(path) -> list\nThe segments (__TEXT, __DATA...) with their sections and permissions.")
    def segments(line, path):
        return parse(line, path)["segments"]

    @m.fn("libraries", 1, doc="libraries(path) -> list\nThe dylibs it loads: [{\"name\", \"kind\", "
                              "\"current_version\", \"compatibility_version\"}].")
    def libraries(line, path):
        return parse(line, path)["libraries"]

    @m.fn("imports", 1, doc="imports(path) -> list\nUndefined external symbols: what it takes from libraries, like \"_printf\".")
    def imports(line, path):
        return parse(line, path)["imports"]

    @m.fn("exports", 1, doc="exports(path) -> list\nExternal symbols the file defines for others.")
    def exports(line, path):
        return parse(line, path)["exports"]

    @m.fn("indicators", 1, doc="indicators(path_or_info) -> list\nSuspicious indicators for a file (or for the map "
                               "macho.parse() gives back): [{\"level\": high/medium/low/info, \"name\", \"detail\"}].")
    def indicators(line, target):
        if isinstance(target, dict):
            if "segments" not in target or "code_signature" not in target:
                fail(line, "macho.indicators() needs a path or the map macho.parse() gives back.")
            return indicators_for(target)
        return parse(line, target)["indicators"]

    @m.fn("summary", 1, doc="summary(path) -> text\nA short description, like \"macho64 arm64 executable (PIE), "
                            "4 segments, needs 1 library, 2 imports, signed ad hoc\".")
    def summary(line, path):
        return describe(parse(line, path))

    return m
