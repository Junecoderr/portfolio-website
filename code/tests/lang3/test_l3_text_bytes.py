"""TB 3.0 Part A3 (text) and A4 (bytes, binary, fs bytes, hash on bytes)."""

import pytest

from conftest import run_tb, tb_error


def test_escapes():
    assert run_tb(r'say("a\tb|\x41|\u{48}\u{49}|\0|\\|\"|\'".len())') == ["16"]
    assert run_tb(r'say("\x41\u{1F600}")') == ["A\U0001F600"]


def test_bad_escape_is_friendly():
    assert "Oops!" in str(tb_error(r'say("\xZZ")'))


def test_raw_and_multiline_text():
    assert run_tb(r'say(r"C:\new\table")') == [r"C:\new\table"]
    assert run_tb('say("""one\ntwo""")') == ["one\ntwo"]
    assert run_tb('let n = 3\nsay(f"""n is\n{n}""")') == ["n is\n3"]
    assert run_tb('say(r"""a\\n\nb""")') == ["a\\n\nb"]


@pytest.mark.parametrize("spec, value, expected", [
    (".2f", "3.14159", "3.14"),
    ("8", "42", "      42"),
    (">8", "42", "      42"),
    ("<8", "42", "42      "),
    ("^8", "42", "   42   "),
    ("08.3f", "3.14159", "0003.142"),
    (",", "1234567", "1,234,567"),
    ("x", "255", "ff"),
    ("X", "255", "FF"),
    ("b", "5", "101"),
    ("o", "8", "10"),
    (".1%", "0.256", "25.6%"),
    (".5", '"hello world"', "hello"),
    ("*^7", '"hi"', "**hi***"),
])
def test_format_specs(spec, value, expected):
    assert run_tb(f'let v = {value}\nsay(f"[{{v:{spec}}}]")') == [f"[{expected}]"]
    assert run_tb(f'say("[" + format({value}, "{spec}") + "]")') == [f"[{expected}]"]


def test_repr_conversion():
    assert run_tb('let s = "hi"\nsay(f"{s!r} {s}")') == ['"hi" hi']


def test_unknown_format_spec_is_friendly():
    err = tb_error('say(f"{3:zz}")')
    assert "format" in str(err).lower()


def test_text_slices_and_indexes():
    assert run_tb('let s = "hello"\nsay(s[1:3], s[-1], s[:2], s[-3:], s[::-1], s[::2])') == [
        "el o he llo olleh hlo"
    ]


def test_bytes_literal_and_type():
    assert run_tb(r'let b = b"AB\x00\xff"' + "\nsay(b, type(b), len(b), b.len(), b.hex())") == [
        r'b"AB\0\xff" bytes 4 4 414200ff'
    ]


def test_bytes_methods():
    src = r'''let b = b"a,b,c"
say(b[0], b.at(-1), b.slice(2), b.slice(0, 1), b.to_text(), b.starts_with(b"a,"), b.find(b"c"))
say(b.split(b","), b"-".join([b"x", b"y"]), b"ab".reverse(), b"ab".to_list(), b.copy() == b)
say(b + b"!", b"ab" == b"ab", b"ab" == "ab")
let total = 0
for byte in b"\x01\x02\x03" { total += byte }
say(total, b"hello"[1:3], "hé".bytes(), "hé".bytes().to_text())'''
    assert run_tb(src) == [
        '97 99 b"b,c" b"a" a,b,c true 4',
        '[b"a", b"b", b"c"] b"x-y" b"ba" [97, 98] true',
        'b"a,b,c!" true false',
        r'6 b"el" b"h\xc3\xa9" hé',
    ]


def test_bytes_literal_must_be_ascii():
    assert "Oops!" in str(tb_error('say(b"é")'))


def test_bytes_index_out_of_range():
    assert "Oops!" in str(tb_error('say(b"ab"[5])'))


def test_binary_library_pack_unpack():
    src = '''import binary
let data = binary.pack("<HI", 1, 2)
say(data, binary.unpack("<HI", data), binary.size("<HI"))
say(binary.unpack(">I4sx", binary.pack(">I4sx", 7, b"abcd")))
say(binary.unpack("<d", binary.pack("<d", 2.5)), binary.unpack("<h", binary.pack("<h", -2)))
say(binary.unpack("<H", b"\\x00\\x01\\x02", 1))'''
    assert run_tb(src) == [
        r'b"\x01\0\x02\0\0\0" [1, 2] 6',
        '[7, b"abcd"]',
        "[2.5] [-2]",
        "[513]",
    ]


def test_binary_helpers():
    src = '''import binary
say(binary.from_hex("4d 5a"), binary.to_hex(b"MZ"), binary.to_hex(b"MZ", ":"), binary.from_list([77, 90]))
say(binary.read_u16le(b"\\x01\\x02"), binary.read_u16be(b"\\x01\\x02"), binary.read_i8(b"\\xff"), binary.read_u32be(b"\\x00\\x00\\x01\\x00"))
say(binary.entropy(b"aaaa"), binary.entropy(binary.from_list(range(256))), binary.xor(binary.xor(b"hi", b"k"), b"k"))
say(binary.concat(b"a", b"b"), binary.concat([b"c", b"d"]), binary.zeros(2))'''
    assert run_tb(src) == [
        'b"MZ" 4d5a 4d:5a b"MZ"',
        "513 258 -1 256",
        '0 8 b"hi"',
        r'b"ab" b"cd" b"\0\0"',
    ]


def test_binary_hexdump():
    out = run_tb('import binary\nsay(binary.hexdump(b"hello world, TB!"))')
    assert out == ["00000000  68 65 6c 6c 6f 20 77 6f 72 6c 64 2c 20 54 42 21  |hello world, TB!|"]


@pytest.mark.parametrize("src, part", [
    ('binary.pack("<B", 300)', "doesn't fit in an unsigned byte"),
    ('binary.pack("<Z", 1)', "doesn't understand 'Z'"),
    ('binary.pack("<HH", 1)', "needs 2 values"),
    ('binary.unpack("<I", b"ab")', "needs 4 bytes at offset 0"),
    ('binary.from_hex("xyz")', "needs hex text"),
    ('binary.from_list([1, 999])', "0 to 255"),
    ('binary.hexdump("text")', "needs data to be bytes"),
])
def test_binary_errors(src, part):
    assert part in str(tb_error("import binary\n" + src))


def test_binary_errors_are_typed():
    src = '''import binary
try { binary.from_hex("zz") } catch e: ParseError { say("parse") }
try { binary.pack("<B", 300) } catch e: ValueError { say("value") }
try { binary.hexdump(1) } catch e: TypeError { say("type") }'''
    assert run_tb(src) == ["parse", "value", "type"]


def test_binary_file_type_and_sniff(tmp_path):
    exe = tmp_path / "a.bin"
    exe.write_bytes(b"%PDF-1.7\n" + b"\0" * 64)
    out = run_tb(f'import binary\nlet t = binary.file_type("{exe.as_posix()}")\nsay(t.type, binary.sniff(b"\\x89PNG\\r\\n\\x1a\\n").type)')
    assert out == ["pdf png"]


def test_fs_bytes_and_streams(tmp_path):
    path = (tmp_path / "x.bin").as_posix()
    text_path = (tmp_path / "t.txt").as_posix()
    src = f'''import fs
fs.write_bytes("{path}", b"\\x00\\x01hi\\nthere\\n")
let d = fs.read_bytes("{path}")
say(d, len(d))
let f = fs.open("{path}", "rb")
say(f.read(2), f.tell(), f.read_line(), f.read_line(), f.read_line())
f.seek(0)
say(f.read())
f.close()
f.close()
say(f.closed())
let n = fs.with_file("{text_path}", "w", fh => {{
  fh.write("one\\n")
  fh.write(2)
  return 7
}})
say(n, fs.read("{text_path}"))
say(fs.with_file("{text_path}", "r", fh => fh.read_line()))
let a = fs.open("{text_path}", "a")
a.write("\\nthree")
a.close()
say(fs.lines("{text_path}"))'''
    assert run_tb(src) == [
        r'b"\0\x01hi\nthere\n" 11',
        r'b"\0\x01" 2 b"hi" b"there" nil',
        r'b"\0\x01hi\nthere\n"',
        "true",
        "7 one\n2",
        "one",
        '["one", "2", "three"]',
    ]


def test_fs_stream_errors(tmp_path):
    path = (tmp_path / "x.txt").as_posix()
    src = f'''import fs
fs.write("{path}", "hi")
let f = fs.open("{path}")
f.close()
try {{ f.read() }} catch e: FileError {{ say("closed") }}
try {{ fs.open("{path}", "q") }} catch e: ValueError {{ say("mode") }}
try {{ fs.write_bytes("{path}", "text") }} catch e: TypeError {{ say("type") }}
try {{ fs.read_bytes("{tmp_path.as_posix()}/nope") }} catch e: NotFoundError {{ say("missing", e is FileError) }}
fs.with_file("{path}", "r", fh => {{ throw Error("inside") }})'''
    with pytest.raises(Exception) as info:
        run_tb(src)
    assert "inside" in str(info.value)
    lines = run_tb(src.rsplit("\n", 1)[0])
    assert lines == ["closed", "mode", "type", "missing true"]


def test_hash_accepts_bytes():
    assert run_tb('import hash\nsay(hash.sha256(b"abc") == hash.sha256("abc"), hash.md5(b"\\xff").len())') == [
        "true 32"
    ]
