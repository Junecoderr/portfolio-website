"""pe library: Windows PE headers, sections, imports/exports and indicators."""

import os

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
