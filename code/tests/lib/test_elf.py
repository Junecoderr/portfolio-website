"""elf library: ELF headers, sections, segments, dynamic info and indicators."""

import os

import pytest

from binfixtures import build_elf, build_pe
from conftest import run_tb, tb_error


def tb_path(p):
    return str(p).replace("\\", "/")


def save(tmp_path, data, name="a.out"):
    path = tmp_path / name
    path.write_bytes(data)
    return tb_path(path)


@pytest.mark.parametrize("bits,little", [(64, True), (32, True), (64, False)])
def test_headers_sections_and_dynamic(tmp_path, bits, little):
    path = save(tmp_path, build_elf(bits=bits, little=little, machine=62 if bits == 64 else 3,
                                    needed=("libc.so.6", "libssl.so.3"), imports=("puts", "connect")))
    out = run_tb(f'import elf\nlet i = elf.parse("{path}")\n'
                 'say(i.format, i.endian, i.type, i.is_executable, i.is_static, i.is_stripped)\n'
                 'say(i.interpreter, i.needed, i.imports, i.entry_section)\n'
                 'let names = i.sections.map(fn(s) { return s.name })\nsay(names.contains(".text"), names.contains(".dynsym"))\n'
                 'let text = i.sections.filter(fn(s) { return s.name == ".text" })[0]\nsay(text.executable, text.writable)\n'
                 f'say(elf.is_elf("{path}"), elf.needed("{path}"), elf.imports("{path}"), len(elf.segments("{path}")) > 0)\n'
                 f'say(len(elf.sections("{path}")) == len(names), elf.exports("{path}"))')
    endian = "little" if little else "big"
    assert out[0] == f"elf{bits} {endian} executable true false false"
    assert out[1] == '/lib64/ld-linux-x86-64.so.2 ["libc.so.6", "libssl.so.3"] ["connect", "puts"] .text'
    assert out[2] == "true true"
    assert out[3] == "true false"
    assert out[4] == 'true ["libc.so.6", "libssl.so.3"] ["connect", "puts"] true'
    assert out[5] == "true []"


def test_shared_object_exports_and_summary(tmp_path):
    path = save(tmp_path, build_elf(e_type=3, exports=("plugin_init", "plugin_run"), interp=None), "libx.so")
    out = run_tb(f'import elf\nlet i = elf.parse("{path}")\nsay(i.type, i.is_shared_object, i.is_pie, i.exports)\n'
                 f'say(elf.summary("{path}"))')
    assert out[0] == 'shared_object true false ["plugin_init", "plugin_run"]'
    assert out[1].startswith("elf64 x64 shared object") or "shared" in out[1]
    assert "needs 1 library" in out[1]


def test_indicators(tmp_path):
    bad = build_elf(sections=[(".text", b"\x90" * 64, 1, 6), ("UPX0", os.urandom(4096), 1, 7)], wx_segment=True,
                    imports=("ptrace", "execve", "fork"), interp="/tmp/ld.so", entry_section="UPX0", symtab=False)
    path = save(tmp_path, bad)
    out = run_tb(f'import elf\nlet found = elf.indicators("{path}")\nsay(found.map(fn(x) {{ return x.name }}).join(","))\n'
                 f'say(found[0].level, len(elf.indicators(elf.parse("{path}"))) == len(found))')
    names = out[0].split(",")
    for name in ("writable_executable_segment", "writable_executable_section", "packer_section_name",
                 "high_entropy_section", "unusual_entry_section", "unusual_interpreter"):
        assert name in names, name
    assert any(n.startswith("imports_") for n in names)
    assert out[1] == "high true"
    clean = save(tmp_path, build_elf(imports=("puts",)), "clean")
    assert run_tb(f'import elf\nsay(elf.indicators("{clean}"))') == ["[]"]


def test_stripped_and_no_section_headers(tmp_path):
    path = save(tmp_path, build_elf(symtab=False, no_section_headers=True))
    out = run_tb(f'import elf\nlet i = elf.parse("{path}")\nsay(i.is_stripped, i.has_section_headers, i.sections)\n'
                 'say(i.indicators.map(fn(x) { return x.name }).contains("no_section_headers"))')
    assert out == ["true false []", "true"]


def test_parse_bytes_truncation_and_errors(tmp_path):
    data = build_elf()
    path = save(tmp_path, data)
    assert run_tb(f'import elf\nimport fs\nsay(elf.parse_bytes(fs.read_bytes("{path}")).format)') == ["elf64"]
    cut = save(tmp_path, data[:200], "cut")
    assert run_tb(f'import elf\nlet i = elf.parse("{cut}")\nsay(i.truncated, i.indicators.map(fn(x) {{ return x.name }}).contains("truncated_file"))') == ["true true"]
    assert run_tb(f'import elf\nsay(elf.parse("{path}").truncated)') == ["false"]
    exe = save(tmp_path, build_pe(), "x.exe")
    assert run_tb(f'import elf\nsay(elf.is_elf("{exe}"))') == ["false"]
    msg = str(tb_error(f'import elf\nelf.parse("{exe}")'))
    assert "isn't an ELF" in msg or "not an ELF" in msg
    assert "Windows" in msg
    tiny = save(tmp_path, data[:56], "tiny")
    msg = str(tb_error(f'import elf\nelf.parse("{tiny}")'))
    assert "ELF" in msg and "Something went wrong" not in msg
    assert "I couldn't find" in str(tb_error('import elf\nelf.parse("missing")'))
