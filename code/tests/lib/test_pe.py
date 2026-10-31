"""pe library: Windows PE headers, sections, imports/exports and indicators."""

import os
import struct
import time

import pytest

from binfixtures import build_elf, build_pe
from conftest import run_tb, tb_error


def tb_path(p):
    return str(p).replace("\\", "/")


def save(tmp_path, data, name="x.exe"):
    path = tmp_path / name
    path.write_bytes(data)
    return tb_path(path)


@pytest.mark.parametrize("plus", [True, False])
def test_headers_sections_and_imports(tmp_path, plus):
    path = save(tmp_path, build_pe(plus=plus, imports={"KERNEL32.dll": ["CreateFileW", "ReadFile", "CloseHandle",
                                                                        "WriteFile", "GetLastError", "ExitProcess"],
                                                       "USER32.dll": ["MessageBoxW"]}))
    out = run_tb(f'import pe\nlet i = pe.parse("{path}")\n'
                 'say(i.format, i.architecture, i.is_dll, i.is_signed, i.subsystem, i.compiled)\n'
                 'say(i.sections.map(fn(s) { return s.name }), i.entry_section, i.entry_hex)\n'
                 'say(i.imports["USER32.dll"], i.import_count)\n'
                 'let t = i.sections[0]\nsay(t.readable, t.writable, t.executable, t.entropy > 0)\n'
                 f'say(pe.is_pe("{path}"), pe.exports("{path}"), len(pe.sections("{path}")), pe.imports("{path}").keys())\n'
                 f'say(pe.summary("{path}"))')
    fmt, arch = ("pe64", "x64") if plus else ("pe32", "x86")
    assert out[0] == f"{fmt} {arch} false false windows_console 2024-01-11 14:49:36 UTC"
    assert out[1] == '[".text", ".rdata", ".data", ".idata"] .text 0x1000'
    assert out[2] == '["MessageBoxW"] 7'
    assert out[3] == "true false true true"
    assert out[4] == 'true [] 4 ["KERNEL32.dll", "USER32.dll"]'
    assert out[5] == f"{fmt} {arch} executable, 4 sections, 7 imports, compiled 2024-01-11, unsigned"


def test_dll_exports_and_signature(tmp_path):
    path = save(tmp_path, build_pe(dll=True, exports=["Install", "Run", "Uninstall"], signed=True,
                                   imports={"KERNEL32.dll": ["Sleep", "GetTickCount", "CreateFileW",
                                                             "ReadFile", "CloseHandle", "HeapAlloc"]}), "x.dll")
    out = run_tb(f'import pe\nlet i = pe.parse("{path}")\nsay(i.is_dll, i.is_signed, i.exports, i.export_name)\n'
                 'say(i.indicators.map(fn(x) { return x.name }).contains("unsigned"))')
    assert out == ['true true ["Install", "Run", "Uninstall"] test.dll', "false"]


def test_suspicious_indicators(tmp_path):
    packed = build_pe(sections=[(".text", b"\x90" * 100, 0x60000020),
                                ("UPX1", os.urandom(8192), 0xE0000040)],
                      imports={"KERNEL32.dll": ["VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread"]},
                      entry_section="UPX1", timestamp=0, dll_characteristics=0)
    path = save(tmp_path, packed)
    out = run_tb(f'import pe\nlet found = pe.indicators("{path}")\n'
                 'say(found.map(fn(x) { return x.name }).join(","))\nsay(found[0].level)\n'
                 f'say(len(pe.indicators(pe.parse("{path}"))) == len(found))')
    names = out[0].split(",")
    for name in ("writable_executable_section", "packer_section_name", "imports_injection", "high_entropy_section",
                 "unusual_entry_section", "few_imports", "zero_timestamp", "no_aslr", "no_dep", "unsigned"):
        assert name in names, name
    assert out[1] == "high" and out[2] == "true"
    clean = save(tmp_path, build_pe(imports={"KERNEL32.dll": ["CreateFileW", "ReadFile", "CloseHandle", "WriteFile",
                                                              "GetLastError", "ExitProcess"]}), "clean.exe")
    assert run_tb(f'import pe\nsay(pe.indicators("{clean}").map(fn(x) {{ return x.level }}))') == ['["info"]']


def test_entry_outside_sections_and_overlay(tmp_path):
    path = save(tmp_path, build_pe(entry_section=None, overlay=os.urandom(20000)))
    out = run_tb(f'import pe\nlet i = pe.parse("{path}")\nsay(i.entry_section, i.overlay_size)\n'
                 'say(i.indicators.map(fn(x) { return x.name }).filter(fn(n) { return n == "entry_point_outside_sections" or n == "large_overlay" }))')
    assert out == ["nil 20000", '["entry_point_outside_sections", "large_overlay"]']


def test_parse_bytes_and_errors(tmp_path):
    data = build_pe()
    elf = save(tmp_path, build_elf(), "a.out")
    text = tmp_path / "t.txt"
    text.write_text("MZ but not really")
    assert run_tb(f'import pe\nimport fs\nsay(pe.parse_bytes(fs.read_bytes("{save(tmp_path, data)}")).format)') == ["pe64"]
    assert run_tb(f'import pe\nsay(pe.is_pe("{elf}"))') == ["false"]
    assert "It looks like Linux/Unix executable (ELF)" in str(tb_error(f'import pe\npe.parse("{elf}")'))
    assert "isn't a Windows PE file" in str(tb_error(f'import pe\npe.parse("{tb_path(text)}")'))
    assert "I couldn't find" in str(tb_error('import pe\npe.parse("missing.exe")'))
    # Cut off inside the section table or a section: still parsed, but flagged.
    for size in (300, 1200):
        cut = save(tmp_path, data[:size], f"cut{size}.exe")
        assert run_tb(f'import pe\nlet i = pe.parse("{cut}")\nsay(i.truncated, i.indicators.map(fn(x) {{ return x.name }}).contains("truncated_file"))') == ["true true"]
    # Cut off before the headers end: a friendly error.
    head = save(tmp_path, data[:0x90], "head.exe")
    msg = str(tb_error(f'import pe\npe.parse("{head}")'))
    assert "PE" in msg and "Something went wrong" not in msg
    assert "pe.indicators() needs" in str(tb_error('import pe\npe.indicators({"a": 1})'))


def test_imphash_matches_pefile(tmp_path):
    """pefile's get_imphash(): "dll.function" lower-cased, .dll/.ocx/.sys dropped, ordinals named from its
    ordlookup tables (ws2_32 23 is socket) or "ordN", joined with commas, then md5."""
    import hashlib
    cases = [
        ({"KERNEL32.dll": ["CreateFileW"], "WS2_32.dll": [23, "connect"]},
         "kernel32.createfilew,ws2_32.socket,ws2_32.connect", "8d512191eb8f00c8013cad542266dae6"),
        ({"MSVBVM60.DLL": [100, "__vbaFreeStr"], "comctl32.ocx": ["InitCommonControls"], "drv.sys": ["Go"],
          "lib.v2.so": ["X"]},
         "msvbvm60.ord100,msvbvm60.__vbafreestr,comctl32.initcommoncontrols,drv.go,lib.v2.so.x", None),
    ]
    for imports, canonical, known in cases:
        path = save(tmp_path, build_pe(imports=imports))
        digest = hashlib.md5(canonical.encode()).hexdigest()
        assert known is None or digest == known
        assert run_tb(f'import pe\nsay(pe.parse("{path}").imphash)') == [digest]
    assert run_tb(f'import pe\nsay(pe.parse("{save(tmp_path, build_pe(), "none.exe")}").imphash)') == ["nil"]


def test_rich_header_resources_and_signature(tmp_path):
    from binfixtures import build_cert, build_pkcs7
    signer = build_cert([("CN", "Demo Signer"), ("O", "Example Org")], [("CN", "Demo CA")], 0x1234,
                        "240101000000Z", "270101000000Z")
    ca = build_cert([("CN", "Demo CA")], [("CN", "Demo CA")], 1)
    data = build_pe(rich=[(259, 30795, 10), (260, 30795, 3)],
                    resources=[(16, 1, 1033, b"VERSIONINFO"), (3, "APPICON", 1033, b"\x89PNG"),
                               (10, 7, 0, os.urandom(4096))],
                    signature=build_pkcs7([ca, signer], ([("CN", "Demo CA")], 0x1234)),
                    imports={"KERNEL32.dll": ["CreateFileW", "ReadFile", "CloseHandle", "WriteFile",
                                              "GetLastError", "ExitProcess"]})
    path = save(tmp_path, data)
    out = run_tb(f'import pe\nlet i = pe.parse("{path}")\nlet r = i.rich_header\n'
                 'say(r.offset, r.checksum_valid, r.entries, len(r.hash))\n'
                 'say(i.resources.map(fn(x) { return f"{x.type}/{x.name}/{x.language}/{x.size}" }))\n'
                 'let s = i.signature\nsay(s.type, s.digest_algorithm, s.signer, s.verified, s.problem)\n'
                 'say(s.certificates.map(fn(c) { return c.subject + " | " + c.serial + " | " + str(c.is_signer) }))\n'
                 'say(s.certificates[1].not_before, s.certificates[1].not_after)\n'
                 'say(i.indicators.map(fn(x) { return x.name }))')
    assert out[0] == ('128 true [{"product_id": 259, "build": 30795, "count": 10}, '
                      '{"product_id": 260, "build": 30795, "count": 3}] 32')
    assert out[1] == '["version/1/1033/11", "icon/APPICON/1033/4", "rcdata/7/0/4096"]'
    assert out[2] == "pkcs7 sha256 CN=Demo Signer, O=Example Org false nil"
    assert out[3] == '["CN=Demo CA | 01 | false", "CN=Demo Signer, O=Example Org | 12:34 | true"]'
    assert out[4] == "2024-01-01 00:00:00 UTC 2027-01-01 00:00:00 UTC"
    assert out[5] == '["high_entropy_section", "high_entropy_resource"]'  # the random rcdata fills .rsrc
    # A Rich header edited by hand fails its checksum; a self-signed signer and a garbage signature are flagged.
    tampered = bytearray(data)
    tampered[0x80 + 16] ^= 0x01
    selfsigned = build_pe(signature=build_pkcs7([ca], ([("CN", "Demo CA")], 1)))
    for blob, name in [(bytes(tampered), "rich_header_tampered"), (selfsigned, "self_signed_signer"),
                       (build_pe(signed=True), "damaged_signature")]:
        p = save(tmp_path, blob, name + ".exe")
        assert name in run_tb(f'import pe\nsay(pe.parse("{p}").indicators.map(fn(x) {{ return x.name }}))')[0]
    assert run_tb(f'import pe\nlet i = pe.parse("{save(tmp_path, build_pe(), "plain.exe")}")\n'
                  'say(i.rich_header, i.resources, i.signature)') == ["nil [] nil"]


# -- hostile inputs: each must finish quickly ---------------------------------

def hostile_header(total: int, res_rva: int) -> bytearray:
    """A PE32 with no sections whose headers cover the whole file (so every RVA is a file offset)
    and a resource directory at res_rva."""
    d = bytearray(total)
    d[0:2] = b"MZ"
    struct.pack_into("<I", d, 0x3C, 0x40)
    d[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", d, 0x44, 0x14C, 0, 0, 0, 0, 224, 0x102)
    struct.pack_into("<H", d, 0x58, 0x10B)
    struct.pack_into("<IIHHHHHHIIIIHH", d, 0x78, 0x1000, 0x200, 4, 0, 0, 0, 4, 0, 0, 0xFFFFFFF0, 0xFFFFFFF0, 0, 2, 0x140)
    struct.pack_into("<II", d, 0xB0, 0, 16)  # loader flags, 16 data directories
    struct.pack_into("<II", d, 0xB8 + 16, res_rva, 0x1000)
    return d


def resource_dir(d, off, count):
    struct.pack_into("<IIHHHH", d, off, 0, 0, 0, 0, 0, count)


@pytest.mark.timeout(60)
def test_resource_tree_fan_out_is_bounded():
    # 48 type dirs x 1024 name dirs, each name dir holding 1024 entries: 50M entries before the budget.
    from tblib import pe
    base, types = 0x1000, 48
    name_dirs = base + 16 + 8 * types
    leaves = name_dirs + types * (16 + 8 * 1024)
    d = hostile_header(leaves + 8 * types * 1024 + 16 + 8 * 1024, base)
    resource_dir(d, base, types)
    k = 0
    for i in range(types):
        nd = name_dirs + i * (16 + 8 * 1024)
        struct.pack_into("<II", d, base + 16 + 8 * i, i, 0x80000000 | (nd - base))
        resource_dir(d, nd, 1024)
        for j in range(1024):
            struct.pack_into("<II", d, nd + 16 + 8 * j, j, 0x80000000 | (leaves - base + 8 * k))
            k += 1
    for p in range(leaves, len(d) - 8, 8):  # every language dir claims 1024 entries
        struct.pack_into("<II", d, p, 0, 0x04000400)
    start = time.perf_counter()
    assert pe.PE(bytes(d)).resources() == []
    assert time.perf_counter() - start < 5


@pytest.mark.timeout(60)
def test_resource_leaves_sharing_one_huge_blob_measure_entropy_once():
    from tblib import pe
    base = 0x1000
    d = hostile_header(4 << 20, base)
    d[0xA000:] = os.urandom(len(d) - 0xA000)
    resource_dir(d, base, 4)
    for i in range(4):
        l1, l2 = 0x100 + i * 0x20, 0x200 + i * 0x2010
        struct.pack_into("<II", d, base + 16 + 8 * i, i + 1, 0x80000000 | l1)
        resource_dir(d, base + l1, 1)
        struct.pack_into("<II", d, base + l1 + 16, 1, 0x80000000 | l2)
        resource_dir(d, base + l2, 1024)
        for j in range(1024):
            struct.pack_into("<II", d, base + l2 + 16 + 8 * j, j, 0x8400)
    struct.pack_into("<III", d, base + 0x8400, 0xA000, 0xFFFFFFF0, 0)  # one leaf "4 GB" long
    start = time.perf_counter()
    found = pe.PE(bytes(d)).resources()
    assert time.perf_counter() - start < 5
    assert len(found) == 4096 and {r["size"] for r in found} == {0xFFFFFFF0}
    assert {r["entropy"] for r in found} == {pe.PE(bytes(d)).resources()[0]["entropy"]} and found[0]["entropy"] > 7.9
    assert all(r["entropy"] is None for r in pe.PE(bytes(d)).resources(entropy=False))


@pytest.mark.timeout(60)
def test_rich_header_far_from_the_start_is_ignored():
    from tblib import pe
    n = 8 << 20  # a DOS stub of 8 MB with a valid-looking Rich header at its end
    d = bytearray(n + 0x200)
    d[0:2] = b"MZ"
    struct.pack_into("<I", d, 0x3C, n)
    key = 0x12345678
    struct.pack_into("<I", d, n - 0x40, 0x536E6144 ^ key)
    d[n - 8:n - 4] = b"Rich"
    struct.pack_into("<I", d, n - 4, key)
    p = pe.PE.__new__(pe.PE)
    p.data, p.e_lfanew = bytes(d), n
    start = time.perf_counter()
    assert p.rich_header() is None
    assert time.perf_counter() - start < 1


def indefinite(element: bytes) -> bytes:
    """The same DER element re-encoded with BER's indefinite length."""
    n = element[1]
    head = 2 + (n & 0x7F if n & 0x80 else 0)
    return bytes([element[0], 0x80]) + element[head:] + b"\x00\x00"


def test_signature_signer_by_key_id_ber_lengths_and_bad_dates(tmp_path):
    from binfixtures import build_cert, build_pkcs7
    from tblib import pe
    ca = build_cert([("CN", "Demo CA")], [("CN", "Demo CA")], 1)
    signer = build_cert([("CN", "Key Signer")], [("CN", "Demo CA")], 0x99, ski=b"\xab\xcd\xef")
    by_key = build_pkcs7([ca, signer], b"\xab\xcd\xef")
    info = pe.pkcs7_info(by_key)
    assert (info["problem"], info["signer"]) == (None, "CN=Key Signer")
    assert [c["is_signer"] for c in info["certificates"]] == [False, True]
    assert "_ski" not in info["certificates"][1]
    # The same SignedData with indefinite lengths on the outer SEQUENCE and the [0] wrapper (BER).
    outer = pe.der_items(by_key)[0]
    oid, wrapper = pe.der_items(by_key, outer[1], outer[2])
    inner = by_key[wrapper[1]:wrapper[2]]
    ber = indefinite(bytes([0x30, 0]) + by_key[oid[1] - 2:oid[2]] + indefinite(bytes([0xA0, 0]) + inner))
    info = pe.pkcs7_info(ber + b"\x00" * 6)  # WIN_CERTIFICATE pads to 8 bytes
    assert (info["problem"], info["signer"]) == (None, "CN=Key Signer")
    assert pe.pkcs7_info(b"\x30\x80\x02\x01\x01")["problem"]  # never closed: damaged, not a crash
    # One unreadable UTCTime loses that field only.
    odd = build_cert([("CN", "Odd Dates")], [("CN", "Demo CA")], 7, "XX0101000000Z", "270101000000Z")
    info = pe.pkcs7_info(build_pkcs7([odd], ([("CN", "Demo CA")], 7)))
    assert info["problem"] is None and info["signer"] == "CN=Odd Dates"
    assert (info["certificates"][0]["not_before"], info["certificates"][0]["not_after"]) == \
        (None, "2027-01-01 00:00:00 UTC")
    path = save(tmp_path, build_pe(signature=by_key))
    assert "damaged_signature" not in run_tb(f'import pe\nsay(pe.parse("{path}").indicators.map(fn(x) {{ return x.name }}))')[0]
