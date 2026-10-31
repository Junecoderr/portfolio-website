"""macho library: Mach-O headers, load commands, segments, symbols, code signatures, universal binaries."""

import os
import sys

import pytest

from binfixtures import build_cert, build_elf, build_fat, build_macho, build_pkcs7
from conftest import run_tb, tb_error


def tb_path(p):
    return str(p).replace("\\", "/")


def save(tmp_path, data, name="demo"):
    path = tmp_path / name
    path.write_bytes(data)
    return tb_path(path)


@pytest.mark.parametrize("bits,cpu,little", [(64, "arm64", True), (64, "x64", True), (32, "x86", True),
                                             (32, "powerpc", False)])
def test_headers_segments_symbols(tmp_path, bits, cpu, little):
    path = save(tmp_path, build_macho(bits=bits, cpu=cpu, little=little, rpaths=("@loader_path/../Frameworks",)))
    out = run_tb(f'import macho\nlet i = macho.parse("{path}")\n'
                 'say(i.format, i.architecture, i.endian, i.type, i.is_pie, i.is_fat)\n'
                 'say(i.segments.map(fn(s) { return s.name + ":" + s.protection }))\n'
                 'say(i.sections.map(fn(s) { return s.name }), i.entry_section, i.entry_hex)\n'
                 'say(i.needed, i.rpaths, i.dylinker, i.uuid)\n'
                 'say(i.imports, i.exports, i.platform, i.min_os, i.sdk)\n'
                 'say(i.load_commands.map(fn(c) { return c.command }).contains("code_signature"), i.truncated)\n'
                 f'say(macho.is_macho("{path}"), macho.imports("{path}"), macho.exports("{path}"), '
                 f'len(macho.segments("{path}")), macho.libraries("{path}")[0].kind)')
    endian = "little" if little else "big"
    assert out[0] == f"macho{bits} {cpu} {endian} executable true false"
    assert out[1] == '["__PAGEZERO:---", "__TEXT:r-x", "__DATA:rw-", "__LINKEDIT:r--"]'
    assert out[2].startswith('["__TEXT,__text", "__TEXT,__cstring", "__DATA,__data"] __TEXT,__text 0x')
    assert out[3] == ('["/usr/lib/libSystem.B.dylib"] ["@loader_path/../Frameworks"] /usr/lib/dyld '
                      '00010203-0405-0607-0809-0A0B0C0D0E0F')
    assert out[4] == '["_printf", "_socket"] ["_main"] macos 13.0 14.0'
    assert out[5] == "true false"
    assert out[6] == 'true ["_printf", "_socket"] ["_main"] 4 load'


def test_code_signature_adhoc_and_certificates(tmp_path):
    adhoc = save(tmp_path, build_macho(entitlements="<plist><key>com.apple.security.get-task-allow</key></plist>"))
    out = run_tb(f'import macho\nlet s = macho.parse("{adhoc}").code_signature\n'
                 'say(s.identifier, s.adhoc, s.hash_type, s.has_cms, s.team_id, s.verified)\n'
                 'say(s.entitlements.contains("get-task-allow"))')
    assert out == ["com.example.demo true sha256 false nil false", "true"]
    cert = build_cert([("CN", "Developer ID Application: Example Corp (ABCDE12345)")], [("CN", "Demo CA")], 77)
    cms = build_pkcs7([cert], ([("CN", "Demo CA")], 77))
    signed = save(tmp_path, build_macho(signature="cms", team="ABCDE12345", cms=cms), "signed")
    out = run_tb(f'import macho\nlet i = macho.parse("{signed}")\nlet s = i.code_signature\n'
                 'say(s.adhoc, s.has_cms, s.team_id, s.signer)\n'
                 'say(s.certificates[0].issuer, s.certificates[0].serial, s.certificates[0].is_signer)\n'
                 f'say(macho.summary("{signed}"))')
    assert out[0] == "false true ABCDE12345 CN=Developer ID Application: Example Corp (ABCDE12345)"
    assert out[1] == "CN=Demo CA 4d true"
    assert out[2].endswith("signed by CN=Developer ID Application: Example Corp (ABCDE12345)")
    unsigned = save(tmp_path, build_macho(signature=None), "unsigned")
    out = run_tb(f'import macho\nlet i = macho.parse("{unsigned}")\nsay(i.is_signed, i.code_signature, '
                 'i.indicators.map(fn(x) { return x.name }))')
    assert out == ['false nil ["unsigned"]']


@pytest.mark.parametrize("sid", ["issuer_serial", "ski"])
@pytest.mark.parametrize("ber", [False, True])
@pytest.mark.parametrize("leaf_first", [False, True])
def test_cms_signer_layouts(tmp_path, sid, ber, leaf_first):
    """RFC 5652 SignerIdentifier in both forms, DER or BER (indefinite lengths, as Apple's codesign
    writes), with signedAttrs, and with the leaf certificate first or last in the set."""
    root = build_cert([("CN", "Demo Root CA")], [("CN", "Demo Root CA")], 1, ski=b"\x01" * 20)
    mid = build_cert([("CN", "Demo Intermediate CA")], [("CN", "Demo Root CA")], 2, ski=b"\x02" * 20)
    leaf = build_cert([("CN", "Software Signing"), ("O", "Example Inc.")], [("CN", "Demo Intermediate CA")], 3,
                      ski=b"\x03" * 20)
    # A second certificate with the leaf's issuer but a different serial, so a match must use both keys.
    decoy = build_cert([("CN", "Decoy Signing")], [("CN", "Demo Intermediate CA")], 4, ski=b"\x04" * 20)
    certs = [leaf, decoy, mid, root] if leaf_first else [root, mid, decoy, leaf]
    who = ([("CN", "Demo Intermediate CA")], 3) if sid == "issuer_serial" else b"\x03" * 20
    cms = build_pkcs7(certs, who, signed_attrs=True, ber=ber)
    path = save(tmp_path, build_macho(signature="cms", cms=cms))
    out = run_tb(f'import macho\nlet s = macho.parse("{path}").code_signature\n'
                 'say(s.signer, s.problem)\n'
                 'say(s.certificates.filter(fn(c) { return c.is_signer }).map(fn(c) { return c.serial }))')
    assert out == ["CN=Software Signing, O=Example Inc. nil", '["03"]']


def test_cms_signer_falls_back_to_leaf(tmp_path):
    """A sid that names no certificate in the set: the leaf (issuer of nothing else) is the signer."""
    root = build_cert([("CN", "Demo Root CA")], [("CN", "Demo Root CA")], 1)
    mid = build_cert([("CN", "Demo Intermediate CA")], [("CN", "Demo Root CA")], 2)
    leaf = build_cert([("CN", "Leaf Signing")], [("CN", "Demo Intermediate CA")], 3)
    for who in (b"\x09" * 20, ([("CN", "Somebody Else")], 99)):
        cms = build_pkcs7([mid, leaf, root], who, ber=True)
        path = save(tmp_path, build_macho(signature="cms", cms=cms))
        out = run_tb(f'import macho\nlet s = macho.parse("{path}").code_signature\nsay(s.signer, s.problem)')
        assert out == ["CN=Leaf Signing nil"]
    # A BER blob whose end-of-contents marker is missing is reported, not a traceback.
    cut = build_pkcs7([leaf], ([("CN", "Demo Intermediate CA")], 3), ber=True)[:-2]
    path = save(tmp_path, build_macho(signature="cms", cms=cut))
    out = run_tb(f'import macho\nlet s = macho.parse("{path}").code_signature\nsay(s.signer, s.problem)')
    assert out[0].startswith("nil the signature block is damaged")


def test_universal_binary(tmp_path):
    for fat64 in (False, True):
        path = save(tmp_path, build_fat([build_macho(cpu="x64"), build_macho(cpu="arm64e")], fat64=fat64), "fat")
        out = run_tb(f'import macho\nlet i = macho.parse("{path}")\n'
                     'say(i.format, i.is_fat, i.architectures, i.architecture)\n'
                     'say(i.slices.map(fn(s) { return s.architecture + "@" + str(s.offset) }))\n'
                     'say(i.slices[1].info.architecture, i.slices[1].summary)\n'
                     f'say(macho.summary("{path}"))')
        assert out[0] == 'macho-fat true ["x64", "arm64e"] x64'
        assert out[1] == '["x64@16384", "arm64e@65536"]'
        assert out[2].startswith("arm64e macho64 arm64e executable (PIE)")
        assert out[3].startswith("macho-fat x64+arm64e executable (PIE), 4 segments")


def test_dylib_and_weak_libraries(tmp_path):
    lib = build_macho(filetype=6, exports=("_plugin_init", "_plugin_run"), imports=("_dlopen", "_dlsym"),
                      dylibs=("/usr/lib/libSystem.B.dylib", (0x80000018, "/tmp/libhook.dylib")))
    path = save(tmp_path, lib, "libdemo.dylib")
    out = run_tb(f'import macho\nlet i = macho.parse("{path}")\n'
                 'say(i.type, i.is_dylib, i.dylib_id, i.entry_point, i.exports)\n'
                 'say(i.libraries.map(fn(l) { return l.kind + ":" + l.name }))\n'
                 'say(i.indicators.map(fn(x) { return x.name }))')
    assert out[0] == 'dylib true @rpath/libdemo.dylib nil ["_plugin_init", "_plugin_run"]'
    assert out[1] == '["load:/usr/lib/libSystem.B.dylib", "weak:/tmp/libhook.dylib"]'
    assert out[2] == '["unusual_library_path", "adhoc_signature", "imports_dynamic_loading"]'


def test_indicators_and_hardening(tmp_path):
    bad = build_macho(pie=False, wx=True, stack_exec=True, encrypted=True,
                      imports=("_task_for_pid", "_mach_vm_write", "_ptrace", "___stack_chk_fail"),
                      extra_sections=[("__payload", os.urandom(4096))])
    path = save(tmp_path, bad)
    out = run_tb(f'import macho\nlet i = macho.parse("{path}")\nsay(i.indicators.map(fn(x) {{ return x.name }}))\n'
                 'say(i.hardening)\n'
                 f'say(len(macho.indicators(i)) == len(macho.indicators("{path}")), i.indicators[0].level)')
    names = out[0]
    for name in ("writable_executable_segment", "imports_injection", "high_entropy_section", "executable_stack",
                 "encrypted", "imports_anti_debug", "no_pie", "adhoc_signature"):
        assert f'"{name}"' in names, name
    assert out[1] == ('{"pie": false, "nx_stack": false, "nx_heap": false, "canary": true, "arc": false, '
                      '"signed": true, "hardened_runtime": false, "encrypted": true}')
    assert out[2] == "true high"


def test_parse_bytes_truncation_and_errors(tmp_path):
    data = build_macho()
    path = save(tmp_path, data)
    assert run_tb(f'import macho\nimport fs\nsay(macho.parse_bytes(fs.read_bytes("{path}")).format)') == ["macho64"]
    cut = save(tmp_path, data[:600], "cut")
    out = run_tb(f'import macho\nlet i = macho.parse("{cut}")\n'
                 'say(i.truncated, i.indicators.map(fn(x) { return x.name }).contains("truncated_file"))')
    assert out == ["true true"]
    elf = save(tmp_path, build_elf(), "a.out")
    assert run_tb(f'import macho\nsay(macho.is_macho("{elf}"))') == ["false"]
    msg = str(tb_error(f'import macho\nmacho.parse("{elf}")'))
    assert "isn't a Mach-O file" in msg and "ELF" in msg
    java = save(tmp_path, b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\x00" * 64, "A.class")
    assert "Java class" in str(tb_error(f'import macho\nmacho.parse("{java}")'))
    tiny = save(tmp_path, data[:20], "tiny")
    msg = str(tb_error(f'import macho\nmacho.parse("{tiny}")'))
    assert "Mach-O" in msg and "Something went wrong" not in msg
    # Damaged load commands and a garbage signature are reported, never a traceback.
    broken = bytearray(data)
    broken[36:40] = b"\xff\xff\x00\x00"
    out = run_tb(f'import macho\nsay(macho.parse("{save(tmp_path, bytes(broken), "broken")}").truncated)')
    assert out == ["true"]
    assert "macho.indicators() needs" in str(tb_error('import macho\nmacho.indicators({"a": 1})'))
    assert "I couldn't find" in str(tb_error('import macho\nmacho.parse("missing")'))


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/bin/ls"), reason="needs macOS system binaries")
def test_real_system_binary():
    import subprocess
    out = run_tb('import macho\nlet i = macho.parse("/bin/ls")\n'
                 'say(i.type, i.is_signed, i.code_signature.identifier, i.code_signature.has_cms)\n'
                 'say(i.needed.contains("/usr/lib/libSystem.B.dylib"), len(i.imports) > 20, i.hardening.canary)\n'
                 'say(i.code_signature.signer)')
    assert out[:2] == ["executable true com.apple.ls true", "true true true"]
    # The signer TB reads must be the first Authority codesign reports on this same machine
    # (it differs between macOS versions, so the test asks codesign rather than assuming).
    shown = subprocess.run(["codesign", "-dvvv", "/bin/ls"], capture_output=True, text=True).stderr
    authorities = [line.split("=", 1)[1] for line in shown.splitlines() if line.startswith("Authority=")]
    if authorities:
        assert f"CN={authorities[0]}" in out[2], (out[2], authorities)
