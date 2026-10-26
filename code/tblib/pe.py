"""pe: read Windows executables (PE: .exe, .dll, .sys) without running them."""

from __future__ import annotations

import datetime as dt
import struct

from tblib import _magic
from tblib._api import Module, check_type, fail

MAX_FILE = 512 * 1024 * 1024

MACHINES = {
    0x14C: ("x86", "Intel 386 (32-bit)"), 0x8664: ("x64", "x86-64 (64-bit)"), 0x1C0: ("arm", "ARM"),
    0x1C4: ("arm", "ARM Thumb-2"), 0xAA64: ("arm64", "ARM64"), 0x200: ("ia64", "Intel Itanium"),
    0x1C2: ("arm", "ARM Thumb"), 0x5032: ("riscv32", "RISC-V 32"), 0x5064: ("riscv64", "RISC-V 64"),
    0x14D: ("x86", "Intel 486"), 0x14E: ("x86", "Intel Pentium"), 0x166: ("mips", "MIPS"), 0x1F0: ("powerpc", "PowerPC"),
}

SUBSYSTEMS = {
    0: "unknown", 1: "native", 2: "windows_gui", 3: "windows_console", 5: "os2", 7: "posix", 9: "windows_ce",
    10: "efi_application", 11: "efi_boot_driver", 12: "efi_runtime_driver", 13: "efi_rom", 14: "xbox",
    16: "windows_boot_application",
}

FILE_CHARACTERISTICS = [
    (0x0001, "relocs_stripped"), (0x0002, "executable_image"), (0x0004, "line_numbers_stripped"),
    (0x0008, "local_symbols_stripped"), (0x0020, "large_address_aware"), (0x0100, "32bit_machine"),
    (0x0200, "debug_stripped"), (0x0400, "removable_run_from_swap"), (0x0800, "net_run_from_swap"),
    (0x1000, "system"), (0x2000, "dll"), (0x4000, "uniprocessor_only"),
]

DLL_CHARACTERISTICS = [
    (0x0020, "high_entropy_va"), (0x0040, "dynamic_base"), (0x0080, "force_integrity"), (0x0100, "nx_compat"),
    (0x0200, "no_isolation"), (0x0400, "no_seh"), (0x0800, "no_bind"), (0x1000, "app_container"),
    (0x2000, "wdm_driver"), (0x4000, "control_flow_guard"), (0x8000, "terminal_server_aware"),
]

SECTION_FLAGS = [
    (0x00000020, "code"), (0x00000040, "initialized_data"), (0x00000080, "uninitialized_data"),
    (0x02000000, "discardable"), (0x04000000, "not_cached"), (0x08000000, "not_paged"), (0x10000000, "shared"),
    (0x20000000, "execute"), (0x40000000, "read"), (0x80000000, "write"),
]

DIRECTORIES = ["export", "import", "resource", "exception", "security", "basereloc", "debug", "architecture",
               "globalptr", "tls", "load_config", "bound_import", "iat", "delay_import", "clr", "reserved"]

PACKER_SECTION_NAMES = {
    "upx0": "UPX", "upx1": "UPX", "upx2": "UPX", ".upx": "UPX", ".aspack": "ASPack", ".adata": "ASPack",
    ".nsp0": "NsPack", ".nsp1": "NsPack", "nsp0": "NsPack", ".petite": "Petite", ".themida": "Themida",
    ".vmp0": "VMProtect", ".vmp1": "VMProtect", ".vmp2": "VMProtect", ".enigma1": "Enigma", ".enigma2": "Enigma",
    ".mpress1": "MPRESS", ".mpress2": "MPRESS", ".packed": "a packer", ".perplex": "Perplex", "pec2to": "PECompact",
    ".pec1": "PECompact", ".pec2": "PECompact", ".y0da": "y0da", ".yp": "Y0da", ".taz": "PESpin", ".boom": "The Boomerang",
    ".ccg": "CCG", ".shrink1": "Shrinker", ".shrink2": "Shrinker", ".neolite": "NeoLite", ".neolit": "NeoLite",
    ".rlpack": "RLPack", ".nsp": "NsPack", ".svkp": "SVKP", ".wwpack": "WWPACK", ".wwp32": "WWPACK", "mew": "MEW",
    ".mew": "MEW", ".spack": "Simple Pack", ".kkrunchy": "kkrunchy", "petite": "Petite", "bero": "BeRoEXEPacker",
}

SUSPICIOUS_IMPORTS = {
    "injection": ["VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread", "NtCreateThreadEx", "QueueUserAPC",
                  "SetThreadContext", "NtUnmapViewOfSection", "RtlCreateUserThread", "NtWriteVirtualMemory"],
    "anti_debug": ["IsDebuggerPresent", "CheckRemoteDebuggerPresent", "NtQueryInformationProcess",
                   "OutputDebugStringA", "NtSetInformationThread"],
    "dynamic_loading": ["LoadLibraryA", "LoadLibraryW", "GetProcAddress", "LdrLoadDll"],
    "keylogging": ["SetWindowsHookExA", "SetWindowsHookExW", "GetAsyncKeyState", "GetKeyState", "GetKeyboardState"],
    "network": ["InternetOpenA", "InternetOpenW", "InternetOpenUrlA", "InternetOpenUrlW", "URLDownloadToFileA",
                "URLDownloadToFileW", "WinHttpOpen", "HttpSendRequestA", "HttpSendRequestW", "WSAStartup", "connect",
                "send", "recv", "socket", "InternetReadFile", "WinHttpSendRequest"],
    "persistence": ["RegSetValueExA", "RegSetValueExW", "RegCreateKeyExA", "RegCreateKeyExW", "CreateServiceA",
                    "CreateServiceW", "StartServiceA", "StartServiceW"],
    "process": ["CreateProcessA", "CreateProcessW", "ShellExecuteA", "ShellExecuteW", "ShellExecuteExA",
                "ShellExecuteExW", "WinExec", "OpenProcess", "TerminateProcess", "CreateToolhelp32Snapshot"],
    "crypto": ["CryptEncrypt", "CryptDecrypt", "CryptAcquireContextA", "CryptAcquireContextW", "BCryptEncrypt",
               "CryptGenKey", "CryptDeriveKey"],
    "privilege": ["AdjustTokenPrivileges", "OpenProcessToken", "LookupPrivilegeValueA", "LookupPrivilegeValueW",
                  "ImpersonateLoggedOnUser", "DuplicateTokenEx"],
    "evasion": ["Sleep", "GetTickCount", "QueryPerformanceCounter", "VirtualProtect", "VirtualProtectEx",
                "NtDelayExecution", "GetSystemFirmwareTable"],
    "file_wiping": ["DeleteFileA", "DeleteFileW", "MoveFileExA", "MoveFileExW", "SetFileAttributesA",
                    "SetFileAttributesW"],
}


class PEFormat(Exception):
    pass


def flags_of(value: int, table) -> list:
    return [name for bit, name in table if value & bit]


def cstr(data: bytes, offset: int, limit: int = 4096) -> str:
    end = data.find(b"\x00", offset, offset + limit)
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("latin-1")


class PE:
    """A parsed PE image. `info()` gives the TB map."""

    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise PEFormat("it doesn't start with the MZ header every Windows executable has")
        (self.e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
        off = self.e_lfanew
        if off + 24 > len(data) or data[off:off + 4] != b"PE\x00\x00":
            raise PEFormat("the PE signature is missing (it is a DOS program or a damaged file)")
        (self.machine, self.section_count, self.timestamp, self.symtab_ptr, self.symbol_count,
         self.opt_size, self.characteristics) = struct.unpack_from("<HHIIIHH", data, off + 4)
        self.opt_offset = off + 24
        self.magic = struct.unpack_from("<H", data, self.opt_offset)[0] if self.opt_size >= 2 else 0
        self.plus = self.magic == 0x20B
        if self.magic not in (0x10B, 0x20B):
            raise PEFormat(f"the optional header magic 0x{self.magic:x} is not PE32 or PE32+")
        self.parse_optional()
        self.sections = self.parse_sections()
        self.entry_section = self.section_for_rva(self.entry_point)

    # -- headers ----------------------------------------------------------------

    def parse_optional(self):
        d, o = self.data, self.opt_offset
        (self.linker_major, self.linker_minor, self.size_of_code, self.size_of_init, self.size_of_uninit,
         self.entry_point, self.base_of_code) = struct.unpack_from("<BBIIIII", d, o + 2)
        if self.plus:
            (self.image_base,) = struct.unpack_from("<Q", d, o + 24)
            o2 = o + 32
        else:
            (self.base_of_data, self.image_base) = struct.unpack_from("<II", d, o + 24)
            o2 = o + 32
        (self.section_alignment, self.file_alignment, self.os_major, self.os_minor, self.image_major,
         self.image_minor, self.subsys_major, self.subsys_minor, _win32, self.size_of_image,
         self.size_of_headers, self.checksum, self.subsystem, self.dll_characteristics) = \
            struct.unpack_from("<IIHHHHHHIIIIHH", d, o2)
        o3 = o2 + 40
        if self.plus:
            (self.stack_reserve, self.stack_commit, self.heap_reserve, self.heap_commit) = struct.unpack_from("<QQQQ", d, o3)
            o3 += 32
        else:
            (self.stack_reserve, self.stack_commit, self.heap_reserve, self.heap_commit) = struct.unpack_from("<IIII", d, o3)
            o3 += 16
        (_loader_flags, self.dir_count) = struct.unpack_from("<II", d, o3)
        o3 += 8
        self.directories = {}
        for i in range(min(self.dir_count, 16)):
            if o3 + 8 > len(d):
                break
            rva, size = struct.unpack_from("<II", d, o3)
            o3 += 8
            self.directories[DIRECTORIES[i]] = (rva, size)

    def parse_sections(self) -> list:
        d = self.data
        start = self.opt_offset + self.opt_size
        sections = []
        self.truncated = False
        for i in range(self.section_count):
            o = start + i * 40
            if o + 40 > len(d):
                self.truncated = True
                break
            raw_name = d[o:o + 8]
            name = raw_name.rstrip(b"\x00").decode("latin-1")
            (vsize, va, rsize, rptr, _reloc, _line, _nreloc, _nline, flags) = struct.unpack_from("<IIIIIIHHI", d, o + 8)
            if rsize and rptr + rsize > len(d):
                self.truncated = True
            if rptr >= len(d):
                rsize_eff = 0
            else:
                rsize_eff = min(rsize, len(d) - rptr)
            body = d[rptr:rptr + rsize_eff] if rsize_eff else b""
            sections.append({
                "name": name, "virtual_address": va, "virtual_size": vsize, "raw_size": rsize, "raw_offset": rptr,
                "entropy": _magic.entropy(body), "flags": flags_of(flags, SECTION_FLAGS),
                "readable": bool(flags & 0x40000000), "writable": bool(flags & 0x80000000),
                "executable": bool(flags & 0x20000000), "characteristics": flags,
                "size": rsize_eff,
            })
        return sections

    def section_for_rva(self, rva: int):
        for s in self.sections:
            size = max(s["virtual_size"], s["raw_size"])
            if s["virtual_address"] <= rva < s["virtual_address"] + max(size, 1):
                return s
        return None

    def rva_to_offset(self, rva: int):
        s = self.section_for_rva(rva)
        if s is None:
            if rva < self.size_of_headers:
                return rva
            return None
        off = s["raw_offset"] + (rva - s["virtual_address"])
        return off if 0 <= off < len(self.data) else None

    # -- imports/exports ----------------------------------------------------------

    def imports(self) -> dict:
        rva, size = self.directories.get("import", (0, 0))
        result: dict = {}
        if not rva:
            return result
        off = self.rva_to_offset(rva)
        if off is None:
            return result
        d = self.data
        for _ in range(512):
            if off + 20 > len(d):
                break
            ilt, _stamp, _chain, name_rva, iat = struct.unpack_from("<IIIII", d, off)
            off += 20
            if not (ilt or name_rva or iat):
                break
            name_off = self.rva_to_offset(name_rva)
            dll = cstr(d, name_off, 256) if name_off is not None else f"<bad name 0x{name_rva:x}>"
            names = []
            thunk = self.rva_to_offset(ilt or iat)
            if thunk is not None:
                step = 8 if self.plus else 4
                for _ in range(4096):
                    if thunk + step > len(d):
                        break
                    value = struct.unpack_from("<Q" if self.plus else "<I", d, thunk)[0]
                    thunk += step
                    if value == 0:
                        break
                    ordinal_bit = 1 << (63 if self.plus else 31)
                    if value & ordinal_bit:
                        names.append(f"ordinal_{value & 0xFFFF}")
                    else:
                        hint_off = self.rva_to_offset(value & 0x7FFFFFFF)
                        if hint_off is None or hint_off + 2 >= len(d):
                            names.append(f"<bad 0x{value:x}>")
                        else:
                            names.append(cstr(d, hint_off + 2, 512))
            result[dll] = names
        return result

    def exports(self) -> dict:
        rva, size = self.directories.get("export", (0, 0))
        out = {"name": None, "functions": []}
        if not rva:
            return out
        off = self.rva_to_offset(rva)
        d = self.data
        if off is None or off + 40 > len(d):
            return out
        (_flags, _stamp, _maj, _min, name_rva, base, n_funcs, n_names, funcs_rva, names_rva, ords_rva) = \
            struct.unpack_from("<IIHHIIIIIII", d, off)
        name_off = self.rva_to_offset(name_rva)
        out["name"] = cstr(d, name_off, 256) if name_off is not None else None
        names_off, ords_off, funcs_off = (self.rva_to_offset(names_rva), self.rva_to_offset(ords_rva),
                                          self.rva_to_offset(funcs_rva))
        if names_off is None or ords_off is None:
            return out
        for i in range(min(n_names, 65536)):
            if names_off + 4 * i + 4 > len(d) or ords_off + 2 * i + 2 > len(d):
                break
            fn_name_rva = struct.unpack_from("<I", d, names_off + 4 * i)[0]
            ordinal = struct.unpack_from("<H", d, ords_off + 2 * i)[0]
            fn_name_off = self.rva_to_offset(fn_name_rva)
            fn_name = cstr(d, fn_name_off, 512) if fn_name_off is not None else f"<bad 0x{fn_name_rva:x}>"
            address = None
            if funcs_off is not None and funcs_off + 4 * ordinal + 4 <= len(d):
                address = struct.unpack_from("<I", d, funcs_off + 4 * ordinal)[0]
            out["functions"].append({"name": fn_name, "ordinal": base + ordinal, "address": address})
        return out

    def debug_pdb(self):
        rva, size = self.directories.get("debug", (0, 0))
        if not rva:
            return None
        off = self.rva_to_offset(rva)
        d = self.data
        if off is None:
            return None
        for i in range(min(size // 28, 16)):
            o = off + i * 28
            if o + 28 > len(d):
                break
            (_c, _stamp, _maj, _min, kind, dsize, _drva, dptr) = struct.unpack_from("<IIHHIIII", d, o)
            if kind == 2 and dptr + 24 < len(d) and d[dptr:dptr + 4] == b"RSDS":
                return cstr(d, dptr + 24, 512)
        return None

    # -- the map ----------------------------------------------------------------------

    def info(self) -> dict:
        arch, machine_name = MACHINES.get(self.machine, (f"0x{self.machine:x}", f"unknown machine 0x{self.machine:x}"))
        compiled = None
        if 0 < self.timestamp < 0xFFFFFFFF:
            compiled = dt.datetime.fromtimestamp(self.timestamp, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        imports = self.imports()
        exports = self.exports()
        security = self.directories.get("security", (0, 0))
        chars = flags_of(self.characteristics, FILE_CHARACTERISTICS)
        sections = [{k: v for k, v in s.items() if k != "characteristics"} for s in self.sections]
        info = {
            "format": "pe64" if self.plus else "pe32",
            "architecture": arch, "machine": machine_name, "is_dll": "dll" in chars,
            "is_driver": self.subsystem == 1 or "system" in chars,
            "is_dotnet": bool(self.directories.get("clr", (0, 0))[0]),
            "is_signed": bool(security[0] and security[1]),
            "subsystem": SUBSYSTEMS.get(self.subsystem, f"unknown ({self.subsystem})"),
            "timestamp": self.timestamp, "compiled": compiled,
            "entry_point": self.entry_point, "entry_hex": f"0x{self.entry_point:x}",
            "entry_section": self.entry_section["name"] if self.entry_section else None,
            "image_base": self.image_base, "size_of_image": self.size_of_image, "size_of_headers": self.size_of_headers,
            "checksum": self.checksum, "linker_version": f"{self.linker_major}.{self.linker_minor}",
            "os_version": f"{self.os_major}.{self.os_minor}",
            "characteristics": chars, "dll_characteristics": flags_of(self.dll_characteristics, DLL_CHARACTERISTICS),
            "sections": sections, "imports": imports,
            "import_count": sum(len(v) for v in imports.values()),
            "exports": [f["name"] for f in exports["functions"]], "export_name": exports["name"],
            "export_details": exports["functions"],
            "pdb": self.debug_pdb(),
            "directories": {k: {"rva": v[0], "size": v[1]} for k, v in self.directories.items() if v[0] or v[1]},
            "overlay_size": max(0, len(self.data) - self.end_of_sections()),
            "truncated": self.truncated,
            "size": len(self.data),
            "entropy": _magic.entropy(self.data),
        }
        info["indicators"] = indicators_for(info)
        return info

    def end_of_sections(self) -> int:
        end = self.size_of_headers
        for s in self.sections:
            end = max(end, s["raw_offset"] + s["raw_size"])
        return min(end, len(self.data)) if self.sections else min(end, len(self.data))


def indicators_for(info: dict) -> list:
    """Things an analyst would raise an eyebrow at. Each is {"level", "name", "detail"}."""
    found = []

    def add(level, name, detail):
        found.append({"level": level, "name": name, "detail": detail})

    for s in info["sections"]:
        if s["entropy"] >= 7.2 and s["size"] >= 512:
            add("medium", "high_entropy_section",
                f'Section {s["name"] or "(unnamed)"} has entropy {s["entropy"]} (packed or encrypted data).')
        if s["writable"] and s["executable"]:
            add("high", "writable_executable_section",
                f'Section {s["name"] or "(unnamed)"} is both writable and executable (self-modifying or unpacking code).')
        lowered = s["name"].lower()
        if lowered in PACKER_SECTION_NAMES:
            add("high", "packer_section_name", f'Section name {s["name"]} belongs to {PACKER_SECTION_NAMES[lowered]}.')
        elif s["name"] and not s["name"].startswith(".") and lowered not in ("code", "data", "text", "bss", "const"):
            add("low", "unusual_section_name", f'Section name {s["name"]} is not one compilers normally produce.')
        if s["raw_size"] == 0 and s["virtual_size"] > 0 and s["executable"]:
            add("medium", "empty_executable_section",
                f'Section {s["name"]} is executable but has no bytes in the file (filled at run time).')
    if info.get("truncated"):
        add("medium", "truncated_file",
            "The file is shorter than its headers say: it was cut off (a partial download or a carved fragment).")
    entry = info["entry_section"]
    if entry is None:
        add("high", "entry_point_outside_sections", "The entry point is not inside any section.")
    elif entry.lower() not in (".text", "code", ".code", "text", ".init", ".itext", ".textbss"):
        add("medium", "unusual_entry_section", f"The entry point is in section {entry}, not .text.")
    if info["entropy"] >= 7.0:
        add("medium", "high_overall_entropy", f'The whole file has entropy {info["entropy"]}; it is likely packed.')
    if not info["imports"] and not info["is_dotnet"]:
        add("medium", "no_imports", "No import table: the program hides which Windows functions it uses.")
    elif info["import_count"] <= 5 and not info["is_dotnet"]:
        names = [n for fns in info["imports"].values() for n in fns]
        add("medium", "few_imports",
            f"Only {info['import_count']} imports ({', '.join(names)}): typical of a packed program that resolves the rest at run time.")
    for group, names in SUSPICIOUS_IMPORTS.items():
        hits = [n for fns in info["imports"].values() for n in fns if n in names]
        if group == "evasion" and len(hits) < 2:
            continue
        if group == "dynamic_loading" and len(hits) < 2:
            continue
        if hits:
            level = {"injection": "high", "keylogging": "high", "anti_debug": "medium", "privilege": "medium"}.get(group, "low")
            add(level, f"imports_{group}", f"Imports {group.replace('_', ' ')} functions: {', '.join(sorted(set(hits))[:8])}.")
    if info["timestamp"] == 0:
        add("low", "zero_timestamp", "The compile timestamp is zero (deliberately blanked).")
    elif info["compiled"]:
        year = int(info["compiled"][:4])
        if year < 1995 or year > dt.datetime.now().year + 1:
            add("low", "odd_timestamp", f"The compile timestamp {info['compiled']} is not believable.")
    if info["overlay_size"] > 0 and info["overlay_size"] > info["size"] * 0.5:
        add("medium", "large_overlay",
            f"{info['overlay_size']:,} bytes sit after the last section (an overlay: often a payload or installer data).")
    if not info["is_signed"]:
        add("info", "unsigned", "No Authenticode signature directory.")
    if "dynamic_base" not in info["dll_characteristics"] and not info["is_dotnet"]:
        add("low", "no_aslr", "Built without ASLR (dynamic base).")
    if "nx_compat" not in info["dll_characteristics"] and not info["is_dotnet"]:
        add("low", "no_dep", "Built without DEP (NX compatibility).")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    found.sort(key=lambda i: order.get(i["level"], 9))
    return found


def parse_bytes(data: bytes) -> dict:
    return PE(data).info()


def parse_or_fail(line, data: bytes, fn: str, path=None) -> dict:
    try:
        return parse_bytes(data)
    except PEFormat as err:
        what = f"'{path}'" if path else "that data"
        kind = _magic.sniff(data)
        hint = "" if kind["type"] in ("pe", "mz", "unknown") else f" It looks like {kind['description']}."
        fail(line, f"{what} isn't a Windows PE file: {err}.{hint}", path=path)
    except (struct.error, IndexError, ValueError) as err:
        what = f"'{path}'" if path else "that data"
        fail(line, f"{what} is a damaged or cut-off PE file ({err}).", path=path)


def load(api):
    m = Module(
        "pe",
        "Read Windows executables (PE: .exe, .dll, .sys) without running them: architecture, compile time, "
        "entry point, sections with their entropy and permissions, imports and exports, whether the file is "
        "signed, and a list of suspicious indicators (packer section names, writable+executable sections, "
        "injection or keylogging imports, odd entry points). Pure Python; also works in the playground on bytes.",
    )

    @m.fn("parse", 1, doc="parse(path) -> map\nEverything pe knows about a file: {\"format\" (pe32/pe64), "
                          "\"architecture\", \"machine\", \"is_dll\", \"is_driver\", \"is_dotnet\", \"is_signed\", "
                          "\"subsystem\", \"timestamp\", \"compiled\", \"entry_point\", \"entry_hex\" (like \"0x401000\"), \"entry_section\", "
                          "\"image_base\", \"sections\" (list of {name, virtual_address, virtual_size, raw_size, "
                          "raw_offset, size, entropy, flags, readable, writable, executable}), \"imports\" "
                          "(map dll -> list of names), \"import_count\", \"exports\" (list of names), "
                          "\"export_name\", \"pdb\", \"directories\", \"overlay_size\", \"truncated\" (cut off), \"entropy\", \"size\" and "
                          "\"indicators\" (list of {level, name, detail}). Fails if the file isn't a PE.")
    def parse(line, path):
        data = _magic.read_file(line, path, "pe.parse", MAX_FILE)
        info = parse_or_fail(line, data, "pe.parse", path)
        info["path"] = path
        return info

    @m.fn("parse_bytes", 1, doc="parse_bytes(data) -> map\nLike pe.parse() but for bytes already in memory.")
    def parse_bytes_fn(line, data):
        raw = _magic.to_bytes(line, data, "pe.parse_bytes")
        return parse_or_fail(line, raw, "pe.parse_bytes")

    @m.fn("is_pe", 1, doc="is_pe(path) -> bool\nTrue if the file starts like a Windows PE executable (MZ header and PE signature).")
    def is_pe(line, path):
        head = _magic.read_head(line, path, "pe.is_pe", 4096)
        return _magic.sniff(head)["type"] == "pe"

    @m.fn("sections", 1, doc="sections(path) -> list\nJust the sections: name, sizes, offsets, entropy and permissions.")
    def sections(line, path):
        return parse(line, path)["sections"]

    @m.fn("imports", 1, doc="imports(path) -> map\nThe import table: {\"KERNEL32.dll\": [\"CreateFileW\", ...], ...}.")
    def imports(line, path):
        return parse(line, path)["imports"]

    @m.fn("exports", 1, doc="exports(path) -> list\nThe names a DLL exports (empty for most .exe files).")
    def exports(line, path):
        return parse(line, path)["exports"]

    @m.fn("indicators", 1, doc="indicators(path_or_info) -> list\nSuspicious indicators for a file (or for a map "
                               "from pe.parse()): [{\"level\": high/medium/low/info, \"name\", \"detail\"}].")
    def indicators(line, target):
        if isinstance(target, dict):
            if "sections" not in target or "imports" not in target:
                fail(line, "pe.indicators() needs a path or the map pe.parse() gives back.")
            return indicators_for(target)
        return parse(line, target)["indicators"]

    @m.fn("summary", 1, doc="summary(path) -> text\nA short human-readable description, like "
                            "\"pe64 x64 executable, 3 sections, 42 imports, compiled 2024-01-05, unsigned\".")
    def summary(line, path):
        info = parse(line, path)
        return describe(info)

    return m


def describe(info: dict) -> str:
    kind = "driver" if info["is_driver"] else "DLL" if info["is_dll"] else "executable"
    if info["is_dotnet"]:
        kind = ".NET " + kind
    parts = [f'{info["format"]} {info["architecture"]} {kind}', f'{len(info["sections"])} sections',
             f'{info["import_count"]} imports']
    if info["exports"]:
        parts.append(f'{len(info["exports"])} exports')
    if info["compiled"]:
        parts.append(f'compiled {info["compiled"][:10]}')
    parts.append("signed" if info["is_signed"] else "unsigned")
    high = sum(1 for i in info["indicators"] if i["level"] == "high")
    if high:
        parts.append(f"{high} high-risk indicator{'s' if high != 1 else ''}")
    return ", ".join(parts)
