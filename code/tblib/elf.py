"""elf: read Linux/Unix executables and shared libraries (ELF) without running them."""

from __future__ import annotations

import struct

from tblib import _magic
from tblib._api import Module, check_type, fail

MAX_FILE = 512 * 1024 * 1024

TYPES = {0: "none", 1: "relocatable", 2: "executable", 3: "shared_object", 4: "core"}
MACHINES = {
    0: ("none", "no machine"), 2: ("sparc", "SPARC"), 3: ("x86", "Intel 386"), 8: ("mips", "MIPS"),
    20: ("powerpc", "PowerPC"), 21: ("powerpc64", "PowerPC64"), 40: ("arm", "ARM"), 50: ("ia64", "Intel Itanium"),
    62: ("x64", "x86-64"), 183: ("arm64", "AArch64"), 243: ("riscv", "RISC-V"), 247: ("bpf", "BPF"),
    43: ("sparcv9", "SPARC V9"), 22: ("s390", "IBM S/390"), 42: ("superh", "SuperH"), 15: ("parisc", "PA-RISC"),
    106: ("blackfin", "Blackfin"), 94: ("xtensa", "Xtensa"), 258: ("loongarch", "LoongArch"),
}
OS_ABI = {0: "system_v", 1: "hp_ux", 2: "netbsd", 3: "linux", 6: "solaris", 7: "aix", 8: "irix", 9: "freebsd",
          10: "tru64", 12: "openbsd", 13: "openvms", 64: "arm_eabi", 97: "arm", 255: "standalone"}
SECTION_TYPES = {
    0: "null", 1: "progbits", 2: "symtab", 3: "strtab", 4: "rela", 5: "hash", 6: "dynamic", 7: "note", 8: "nobits",
    9: "rel", 10: "shlib", 11: "dynsym", 14: "init_array", 15: "fini_array", 16: "preinit_array", 17: "group",
    18: "symtab_shndx", 0x6FFFFFF6: "gnu_hash", 0x6FFFFFFD: "gnu_verdef", 0x6FFFFFFE: "gnu_verneed",
    0x6FFFFFFF: "gnu_versym",
}
SECTION_FLAGS = [(0x1, "write"), (0x2, "alloc"), (0x4, "exec"), (0x10, "merge"), (0x20, "strings"),
                 (0x40, "info_link"), (0x200, "group"), (0x400, "tls")]
SEGMENT_TYPES = {0: "null", 1: "load", 2: "dynamic", 3: "interp", 4: "note", 5: "shlib", 6: "phdr", 7: "tls",
                 0x6474E550: "gnu_eh_frame", 0x6474E551: "gnu_stack", 0x6474E552: "gnu_relro", 0x6474E553: "gnu_property"}
SEGMENT_FLAGS = [(0x4, "read"), (0x2, "write"), (0x1, "exec")]
DYN_TAGS = {1: "needed", 5: "strtab", 6: "symtab", 10: "strsz", 11: "syment", 14: "soname", 15: "rpath", 29: "runpath",
            12: "init", 13: "fini", 0x6FFFFFFB: "flags_1", 30: "flags", 21: "debug", 22: "textrel", 24: "bind_now"}
SYMBOL_TYPES = {0: "notype", 1: "object", 2: "func", 3: "section", 4: "file", 5: "common", 6: "tls", 10: "gnu_ifunc"}
SYMBOL_BINDS = {0: "local", 1: "global", 2: "weak", 10: "gnu_unique"}

PACKER_SECTION_NAMES = {"upx0": "UPX", "upx1": "UPX", "upx2": "UPX", ".upx": "UPX", "upx!": "UPX"}

SUSPICIOUS_IMPORTS = {
    "process_injection": ["ptrace", "process_vm_writev", "process_vm_readv"],
    "dynamic_loading": ["dlopen", "dlsym"],
    "network": ["socket", "connect", "bind", "listen", "accept", "sendto", "recvfrom", "getaddrinfo", "gethostbyname",
                "curl_easy_perform", "inet_addr"],
    "execution": ["execve", "execv", "execvp", "execl", "system", "popen", "fork", "posix_spawn", "clone"],
    "persistence": ["crontab", "setuid", "setgid", "setresuid"],
    "anti_debug": ["ptrace", "prctl"],
    "memory_tricks": ["mprotect", "mmap", "memfd_create"],
    "pty_or_shell": ["openpty", "forkpty", "grantpt", "unlockpt", "ptsname"],
    "wiping": ["unlink", "unlinkat", "shred", "truncate"],
    "keylogging": ["XQueryKeymap", "XGrabKey", "XRecordEnableContext"],
}


class ELFFormat(Exception):
    pass


def flags_of(value: int, table) -> list:
    return [name for bit, name in table if value & bit]


def cstr(data: bytes, offset: int, limit: int = 4096) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset, offset + limit)
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("utf-8", "replace")


class ELF:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 52 or data[:4] != b"\x7fELF":
            raise ELFFormat("it doesn't start with the \\x7fELF magic bytes")
        self.bits = {1: 32, 2: 64}.get(data[4])
        if not self.bits:
            raise ELFFormat(f"EI_CLASS {data[4]} is neither 32- nor 64-bit")
        self.endian = {1: "<", 2: ">"}.get(data[5])
        if not self.endian:
            raise ELFFormat(f"EI_DATA {data[5]} is neither little- nor big-endian")
        self.os_abi = data[7]
        e = self.endian
        if self.bits == 64:
            (self.e_type, self.machine, _ver, self.entry, self.phoff, self.shoff, self.flags, self.ehsize,
             self.phentsize, self.phnum, self.shentsize, self.shnum, self.shstrndx) = \
                struct.unpack_from(e + "HHIQQQIHHHHHH", data, 16)
        else:
            (self.e_type, self.machine, _ver, self.entry, self.phoff, self.shoff, self.flags, self.ehsize,
             self.phentsize, self.phnum, self.shentsize, self.shnum, self.shstrndx) = \
                struct.unpack_from(e + "HHIIIIIHHHHHH", data, 16)
        self.segments = self.parse_segments()
        self.sections = self.parse_sections()
        self.entry_section = self.section_for_address(self.entry)
        n = len(data)
        self.truncated = bool(
            (self.phoff and self.phoff + self.phnum * self.phentsize > n)
            or (self.shoff and self.shoff + self.shnum * self.shentsize > n)
            or any(s["type_id"] != 8 and s["size"] and s["offset"] + s["size"] > n for s in self.sections)
            or any(seg["file_size"] and seg["offset"] + seg["file_size"] > n for seg in self.segments))

    def parse_segments(self) -> list:
        out = []
        d, e = self.data, self.endian
        if not self.phoff or not self.phnum:
            return out
        for i in range(min(self.phnum, 256)):
            o = self.phoff + i * self.phentsize
            if o + self.phentsize > len(d) or self.phentsize < (56 if self.bits == 64 else 32):
                break
            if self.bits == 64:
                p_type, p_flags, offset, vaddr, _paddr, filesz, memsz, align = struct.unpack_from(e + "IIQQQQQQ", d, o)
            else:
                p_type, offset, vaddr, _paddr, filesz, memsz, p_flags, align = struct.unpack_from(e + "IIIIIIII", d, o)
            out.append({"type": SEGMENT_TYPES.get(p_type, f"0x{p_type:x}"), "flags": flags_of(p_flags, SEGMENT_FLAGS),
                        "offset": offset, "address": vaddr, "file_size": filesz, "memory_size": memsz, "align": align,
                        "readable": bool(p_flags & 4), "writable": bool(p_flags & 2), "executable": bool(p_flags & 1)})
        return out

    def parse_sections(self) -> list:
        out = []
        d, e = self.data, self.endian
        if not self.shoff or not self.shnum:
            return out
        raw = []
        for i in range(min(self.shnum, 4096)):
            o = self.shoff + i * self.shentsize
            if o + self.shentsize > len(d) or self.shentsize < (64 if self.bits == 64 else 40):
                break
            if self.bits == 64:
                name, s_type, s_flags, addr, offset, size, link, info, align, entsize = struct.unpack_from(e + "IIQQQQIIQQ", d, o)
            else:
                name, s_type, s_flags, addr, offset, size, link, info, align, entsize = struct.unpack_from(e + "IIIIIIIIII", d, o)
            raw.append((name, s_type, s_flags, addr, offset, size, link, info, align, entsize))
        strtab_off = None
        if self.shstrndx < len(raw):
            strtab_off = raw[self.shstrndx][4]
        for name_idx, s_type, s_flags, addr, offset, size, link, info, align, entsize in raw:
            name = cstr(d, strtab_off + name_idx, 256) if strtab_off is not None else ""
            body = b""
            if s_type != 8 and offset < len(d):
                body = d[offset:offset + min(size, len(d) - offset)]
            out.append({"name": name, "type": SECTION_TYPES.get(s_type, f"0x{s_type:x}"), "type_id": s_type,
                        "flags": flags_of(s_flags, SECTION_FLAGS), "address": addr, "offset": offset, "size": size,
                        "entropy": _magic.entropy(body) if body else 0.0, "link": link, "info": info,
                        "entry_size": entsize, "writable": bool(s_flags & 1), "allocated": bool(s_flags & 2),
                        "executable": bool(s_flags & 4)})
        return out

    def section_for_address(self, address: int):
        for s in self.sections:
            if s["allocated"] and s["size"] and s["address"] <= address < s["address"] + s["size"]:
                return s
        return None

    def segment_for_address(self, address: int):
        for seg in self.segments:
            if seg["type"] == "load" and seg["address"] <= address < seg["address"] + max(seg["memory_size"], 1):
                return seg
        return None

    def address_to_offset(self, address: int):
        seg = self.segment_for_address(address)
        if seg is not None:
            return seg["offset"] + (address - seg["address"])
        s = self.section_for_address(address)
        if s is not None:
            return s["offset"] + (address - s["address"])
        return None

    def section(self, name: str):
        for s in self.sections:
            if s["name"] == name:
                return s
        return None

    def interpreter(self):
        for seg in self.segments:
            if seg["type"] == "interp":
                return cstr(self.data, seg["offset"], 256) or None
        s = self.section(".interp")
        return cstr(self.data, s["offset"], 256) if s else None

    def dynamic(self) -> dict:
        """DT_NEEDED and friends from the .dynamic section/segment."""
        out = {"needed": [], "soname": None, "rpath": None, "runpath": None, "entries": []}
        dyn = self.section(".dynamic")
        off, size = (dyn["offset"], dyn["size"]) if dyn else (None, 0)
        if off is None:
            for seg in self.segments:
                if seg["type"] == "dynamic":
                    off, size = seg["offset"], seg["file_size"]
        if off is None:
            return out
        d, e = self.data, self.endian
        step = 16 if self.bits == 64 else 8
        fmt = e + ("qQ" if self.bits == 64 else "iI")
        entries = []
        for i in range(min(size // step, 2048)):
            o = off + i * step
            if o + step > len(d):
                break
            tag, value = struct.unpack_from(fmt, d, o)
            if tag == 0:
                break
            entries.append((tag, value))
        strtab = self.section(".dynstr")
        strtab_off = strtab["offset"] if strtab else None
        if strtab_off is None:
            for tag, value in entries:
                if tag == 5:
                    strtab_off = self.address_to_offset(value)
        for tag, value in entries:
            name = DYN_TAGS.get(tag, f"0x{tag:x}")
            out["entries"].append({"tag": name, "value": value})
            if strtab_off is not None and tag in (1, 14, 15, 29):
                text = cstr(d, strtab_off + value, 512)
                if tag == 1:
                    out["needed"].append(text)
                else:
                    out[DYN_TAGS[tag]] = text
        return out

    def section_of_type(self, type_id: int):
        for s in self.sections:
            if s["type_id"] == type_id:
                return s
        return None

    def versions(self) -> dict:
        """GNU symbol versioning: {"needs": {file: [versions]}, "defines": [...], "names": {index: version}}."""
        d, e = self.data, self.endian
        out = {"needs": {}, "defines": [], "names": {}}

        def strtab_of(s):
            return self.sections[s["link"]]["offset"] if s["link"] < len(self.sections) else None
        need = self.section_of_type(0x6FFFFFFE)
        if need and strtab_of(need) is not None:
            strs, off = strtab_of(need), need["offset"]
            for _ in range(min(need["info"] or 64, 256)):
                if off + 16 > len(d):
                    break
                _ver, cnt, file_, aux, nxt = struct.unpack_from(e + "HHIII", d, off)
                versions = out["needs"].setdefault(cstr(d, strs + file_, 256), [])
                a = off + aux
                for _ in range(min(cnt, 256)):
                    if a + 16 > len(d):
                        break
                    _hash, _flags, other, name, anext = struct.unpack_from(e + "IHHII", d, a)
                    text = cstr(d, strs + name, 256)
                    versions.append(text)
                    out["names"][other & 0x7FFF] = text
                    if not anext:
                        break
                    a += anext
                if not nxt:
                    break
                off += nxt
        define = self.section_of_type(0x6FFFFFFD)
        if define and strtab_of(define) is not None:
            strs, off = strtab_of(define), define["offset"]
            for _ in range(min(define["info"] or 64, 256)):
                if off + 20 > len(d):
                    break
                _ver, flags, ndx, cnt, _hash, aux, nxt = struct.unpack_from(e + "HHHHIII", d, off)
                if cnt and off + aux + 8 <= len(d):
                    text = cstr(d, strs + struct.unpack_from(e + "I", d, off + aux)[0], 256)
                    if not flags & 1:  # VER_FLG_BASE names the file itself; index 1 means "unversioned"
                        out["names"][ndx & 0x7FFF] = text
                        out["defines"].append(text)
                if not nxt:
                    break
                off += nxt
        return out

    def symbols(self, section_name: str, version_names=None) -> list:
        sym = self.section(section_name)
        if not sym:
            return []
        versym = self.section_of_type(0x6FFFFFFF) if version_names else None
        strtab = self.sections[sym["link"]] if sym["link"] < len(self.sections) else None
        if not strtab:
            return []
        d, e = self.data, self.endian
        out = []
        step = 24 if self.bits == 64 else 16
        for i in range(min(sym["size"] // step, 65536)):
            o = sym["offset"] + i * step
            if o + step > len(d):
                break
            if self.bits == 64:
                name_idx, info, _other, shndx, value, size = struct.unpack_from(e + "IBBHQQ", d, o)
            else:
                name_idx, value, size, info, _other, shndx = struct.unpack_from(e + "IIIBBH", d, o)
            name = cstr(d, strtab["offset"] + name_idx, 512)
            if not name:
                continue
            version = None
            if versym and versym["offset"] + 2 * i + 2 <= len(d):
                version = version_names.get(struct.unpack_from(e + "H", d, versym["offset"] + 2 * i)[0] & 0x7FFF)
            out.append({"name": name, "type": SYMBOL_TYPES.get(info & 0xF, str(info & 0xF)),
                        "bind": SYMBOL_BINDS.get(info >> 4, str(info >> 4)), "value": value, "size": size,
                        "defined": shndx != 0, "version": version})
        return out

    def hardening(self, dynamic: dict, names: set) -> dict:
        """What checksec reports: RELRO, NX, PIE, stack canary and FORTIFY_SOURCE."""
        tags = {}
        for entry in dynamic["entries"]:
            tags[entry["tag"]] = tags.get(entry["tag"], 0) | entry["value"]
        bind_now = "bind_now" in tags or bool(tags.get("flags", 0) & 0x8) or bool(tags.get("flags_1", 0) & 0x1)
        types = {seg["type"]: seg for seg in self.segments}
        relro = ("full" if bind_now else "partial") if "gnu_relro" in types else "none"
        stack = types.get("gnu_stack")
        return {"relro": relro, "bind_now": bind_now, "nx": bool(stack) and not stack["executable"],
                "pie": self.pie(tags), "canary": bool(names & {"__stack_chk_fail", "__stack_chk_guard"}),
                "fortify": any(n.startswith("__") and n.endswith("_chk") and n != "__stack_chk_fail" for n in names),
                "rpath": bool(dynamic["rpath"]), "runpath": bool(dynamic["runpath"])}

    def pie(self, tags: dict) -> bool:
        has_interp = any(seg["type"] == "interp" for seg in self.segments)
        return self.e_type == 3 and (has_interp or bool(tags.get("flags_1", 0) & 0x08000000))

    def info(self) -> dict:
        arch, machine_name = MACHINES.get(self.machine, (f"0x{self.machine:x}", f"unknown machine 0x{self.machine:x}"))
        dynamic = self.dynamic()
        versions = self.versions()
        dynsyms = self.symbols(".dynsym", versions["names"])
        imports = sorted({s["name"] for s in dynsyms if not s["defined"] and s["type"] in ("func", "notype", "gnu_ifunc", "object")})
        exports = sorted({s["name"] for s in dynsyms if s["defined"] and s["bind"] in ("global", "weak") and s["type"] in ("func", "object", "gnu_ifunc")})
        symtab = self.symbols(".symtab")
        hardening = self.hardening(dynamic, {s["name"] for s in dynsyms + symtab})
        info = {
            "format": f"elf{self.bits}", "bits": self.bits, "endian": "little" if self.endian == "<" else "big",
            "architecture": arch, "machine": machine_name,
            "type": TYPES.get(self.e_type, f"0x{self.e_type:x}"), "os_abi": OS_ABI.get(self.os_abi, str(self.os_abi)),
            "is_executable": self.e_type == 2, "is_shared_object": self.e_type == 3,
            "is_pie": hardening["pie"],
            "entry_point": self.entry, "entry_hex": f"0x{self.entry:x}",
            "entry_section": self.entry_section["name"] if self.entry_section else None,
            "interpreter": self.interpreter(),
            "is_static": not dynamic["needed"] and not any(seg["type"] == "interp" for seg in self.segments),
            "is_stripped": not symtab,
            "sections": [{k: v for k, v in s.items() if k not in ("type_id", "link", "info", "entry_size")} for s in self.sections],
            "segments": self.segments,
            "needed": dynamic["needed"], "soname": dynamic["soname"], "rpath": dynamic["rpath"], "runpath": dynamic["runpath"],
            "imports": imports, "exports": exports,
            "version_needs": versions["needs"], "version_definitions": versions["defines"],
            "symbol_versions": {s["name"]: s["version"] for s in dynsyms if s["version"]},
            "hardening": hardening,
            "symbol_count": len(symtab), "size": len(self.data), "entropy": _magic.entropy(self.data),
            "has_section_headers": bool(self.sections), "truncated": self.truncated,
        }
        info["indicators"] = indicators_for(info)
        return info


def indicators_for(info: dict) -> list:
    found = []

    def add(level, name, detail):
        found.append({"level": level, "name": name, "detail": detail})

    for seg in info["segments"]:
        if seg["type"] == "load" and seg["writable"] and seg["executable"]:
            add("high", "writable_executable_segment",
                f"A LOAD segment at 0x{seg['address']:x} is both writable and executable.")
        if seg["type"] == "gnu_stack" and seg["executable"]:
            add("medium", "executable_stack", "The stack is marked executable (GNU_STACK with exec).")
    for s in info["sections"]:
        if s["entropy"] >= 7.2 and s["size"] >= 512 and s["type"] != "nobits":
            add("medium", "high_entropy_section", f"Section {s['name'] or '(unnamed)'} has entropy {s['entropy']} (packed or encrypted data).")
        if s["writable"] and s["executable"]:
            add("high", "writable_executable_section", f"Section {s['name'] or '(unnamed)'} is both writable and executable.")
        low = s["name"].lower()
        if low in PACKER_SECTION_NAMES:
            add("high", "packer_section_name", f"Section name {s['name']} belongs to {PACKER_SECTION_NAMES[low]}.")
    if info.get("truncated"):
        add("medium", "truncated_file",
            "The file is shorter than its headers say: it was cut off (a partial download or a carved fragment).")
    if not info["has_section_headers"]:
        add("medium", "no_section_headers", "No section headers: stripped by a packer or built by hand.")
    elif info["entry_section"] is None and info["type"] in ("executable", "shared_object"):
        add("high", "entry_point_outside_sections", "The entry point is not inside any section.")
    elif info["entry_section"] not in (None, ".text", ".init", ".start", ".plt"):
        add("medium", "unusual_entry_section", f"The entry point is in section {info['entry_section']}, not .text.")
    if info["entropy"] >= 7.0:
        add("medium", "high_overall_entropy", f"The whole file has entropy {info['entropy']}; it is likely packed.")
    if info["is_static"] and info["type"] == "executable" and info["is_stripped"]:
        add("low", "static_stripped", "Statically linked and stripped: hard to tell what it does.")
    for group, names in SUSPICIOUS_IMPORTS.items():
        hits = [n for n in info["imports"] if n in names]
        if group in ("memory_tricks", "wiping", "execution") and len(hits) < 2:
            continue
        if hits:
            level = {"process_injection": "high", "keylogging": "high", "pty_or_shell": "medium", "anti_debug": "medium"}.get(group, "low")
            add(level, f"imports_{group}", f"Imports {group.replace('_', ' ')} functions: {', '.join(sorted(set(hits))[:8])}.")
    if info["rpath"] or info["runpath"]:
        path = info["rpath"] or info["runpath"]
        if any(p in path for p in ("/tmp", "/dev/shm", "$ORIGIN", ".")):
            add("low", "unusual_rpath", f"Libraries are loaded from {path}.")
    if info["interpreter"] and not info["interpreter"].startswith(("/lib", "/usr/lib", "/lib64", "/system/")):
        add("medium", "unusual_interpreter", f"The program interpreter is {info['interpreter']}.")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    found.sort(key=lambda i: order.get(i["level"], 9))
    return found


def parse_bytes(data: bytes) -> dict:
    return ELF(data).info()


def parse_or_fail(line, data: bytes, path=None) -> dict:
    try:
        return parse_bytes(data)
    except ELFFormat as err:
        what = f"'{path}'" if path else "that data"
        kind = _magic.sniff(data)
        hint = "" if kind["type"] in ("elf", "unknown") else f" It looks like {kind['description']}."
        fail(line, f"{what} isn't an ELF file: {err}.{hint}", path=path)
    except (struct.error, IndexError, ValueError) as err:
        what = f"'{path}'" if path else "that data"
        fail(line, f"{what} is a damaged or cut-off ELF file ({err}).", path=path)


def describe(info: dict) -> str:
    parts = [f"{info['format']} {info['architecture']} {info['type'].replace('_', ' ')}"]
    if info["is_pie"]:
        parts[0] += " (PIE)"
    parts.append(f"{len(info['sections'])} sections")
    parts.append("static" if info["is_static"] else f"needs {len(info['needed'])} librar{'y' if len(info['needed']) == 1 else 'ies'}")
    parts.append(f"{len(info['imports'])} imports")
    if info["exports"]:
        parts.append(f"{len(info['exports'])} exports")
    parts.append("stripped" if info["is_stripped"] else "not stripped")
    high = sum(1 for i in info["indicators"] if i["level"] == "high")
    if high:
        parts.append(f"{high} high-risk indicator{'s' if high != 1 else ''}")
    return ", ".join(parts)


def load(api):
    m = Module(
        "elf",
        "Read Linux/Unix executables and shared libraries (ELF) without running them: architecture, type, "
        "entry point, sections and segments with entropy and permissions, the libraries it needs, imported and "
        "exported symbols, the program interpreter, and suspicious indicators (writable+executable memory, "
        "UPX section names, ptrace/dlopen imports, odd interpreters). Pure Python; works on bytes in the playground.",
    )

    @m.fn("parse", 1, doc=(
        "parse(path) -> map\n"
        "Everything elf knows about a file. Fails if the file isn't ELF.\n"
        "\n"
        "Fields:\n"
        "- \"format\": elf32 or elf64.\n"
        "- \"architecture\": the CPU type, like \"x64\" or \"arm64\".\n"
        "- \"machine\": the CPU's longer name, like \"x86-64\".\n"
        "- \"type\": executable, shared_object, relocatable or core.\n"
        "- \"os_abi\", \"endian\"\n"
        "- \"is_pie\", \"is_static\", \"is_stripped\": true or false.\n"
        "- \"entry_point\": the address where the program starts.\n"
        "- \"entry_hex\": the same address in hex, like \"0x401000\".\n"
        "- \"entry_section\": the section the entry point is in.\n"
        "- \"interpreter\": the loader the program asks for.\n"
        "- \"needed\": the libraries it needs.\n"
        "- \"soname\", \"rpath\"\n"
        "- \"sections\": a list of maps with name, type, flags, address, offset, size,\n"
        "  entropy, writable and executable.\n"
        "- \"segments\", \"imports\", \"exports\"\n"
        "- \"version_needs\": the symbol versions it needs per library,\n"
        "  like {\"libc.so.6\": [\"GLIBC_2.34\"]}.\n"
        "- \"version_definitions\": the versions a library defines for others.\n"
        "- \"symbol_versions\": dynamic symbol -> version, like {\"puts\": \"GLIBC_2.2.5\"}.\n"
        "- \"hardening\": the checksec report: relro (full, partial or none), bind_now, nx,\n"
        "  pie, canary, fortify, rpath and runpath.\n"
        "- \"entropy\", \"size\"\n"
        "- \"truncated\": true if the file is cut off.\n"
        "- \"indicators\": a list of {level, name, detail} findings."))
    def parse(line, path):
        data = _magic.read_file(line, path, "elf.parse", MAX_FILE)
        info = parse_or_fail(line, data, path)
        info["path"] = path
        return info

    @m.fn("parse_bytes", 1, doc="parse_bytes(data) -> map\nLike elf.parse() but for bytes already in memory.")
    def parse_bytes_fn(line, data):
        raw = _magic.to_bytes(line, data, "elf.parse_bytes")
        return parse_or_fail(line, raw)

    @m.fn("is_elf", 1, doc="is_elf(path) -> bool\nTrue if the file starts with the ELF magic bytes.")
    def is_elf(line, path):
        return _magic.read_head(line, path, "elf.is_elf", 4)[:4] == b"\x7fELF"

    @m.fn("sections", 1, doc="sections(path) -> list\nJust the sections: name, type, address, size, entropy and flags.")
    def sections(line, path):
        return parse(line, path)["sections"]

    @m.fn("segments", 1, doc="segments(path) -> list\nThe program headers (LOAD, INTERP, DYNAMIC...) with permissions.")
    def segments(line, path):
        return parse(line, path)["segments"]

    @m.fn("imports", 1, doc="imports(path) -> list\nUndefined dynamic symbols: the functions it gets from shared libraries.")
    def imports(line, path):
        return parse(line, path)["imports"]

    @m.fn("exports", 1, doc="exports(path) -> list\nGlobal symbols the file defines for others (what a .so offers).")
    def exports(line, path):
        return parse(line, path)["exports"]

    @m.fn("needed", 1, doc="needed(path) -> list\nThe shared libraries it needs (DT_NEEDED), like [\"libc.so.6\"].")
    def needed(line, path):
        return parse(line, path)["needed"]

    @m.fn("indicators", 1, doc="indicators(path_or_info) -> list\nSuspicious indicators for a file (or for the map "
                               "elf.parse() gives back): [{\"level\": high/medium/low, \"name\", \"detail\"}].")
    def indicators(line, target):
        if isinstance(target, dict):
            if "sections" not in target or "segments" not in target:
                fail(line, "elf.indicators() needs a path or the map elf.parse() gives back.")
            return indicators_for(target)
        return parse(line, target)["indicators"]

    @m.fn("summary", 1, doc="summary(path) -> text\nA short description, like \"elf64 x64 executable (PIE), "
                            "29 sections, needs 1 library, 12 imports, stripped\".")
    def summary(line, path):
        return describe(parse(line, path))

    return m
