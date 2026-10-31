"""evtx library on damaged input: dirty chunks, unknown binary XML tokens, records
carved from slack space, task/opcode names, and a fuzz test of corrupted files."""

import faulthandler
import random
import struct
import time

import tb
from conftest import run_tb
from evtxwriter import Value, build_evtx, security
from tblib import evtx

T = 1773302400.0
CHUNK = 4096  # where the first chunk starts


def tb_path(p):
    return str(p).replace("\\", "/")


def events(n=5):
    return [security(4625, T + i, "WS01.corp.example", {"TargetUserName": f"user{i}", "LogonType": Value.u32(3),
                                                          "IpAddress": "203.0.113.7"},
                     success=False, record_id=i + 1, task=12544) for i in range(n)]


def record_offsets(data):
    """Chunk-relative (pos, size) of every live record in the first chunk."""
    chunk = data[CHUNK:CHUNK + 65536]
    return [(pos, size) for pos, size, *_ in evtx.iter_raw_records(chunk, evtx.chunk_header(chunk))]


def set_free_offset(data: bytearray, offset: int):
    struct.pack_into("<I", data, CHUNK + 48, offset)


def ids(records):
    return [(r["record_id"], bool(r.get("recovered"))) for r in records]


def test_dirty_file_keeps_records_past_a_stale_chunk_header():
    data = bytearray(build_evtx(events(5)))
    offsets = record_offsets(data)
    end_of_third = offsets[2][0] + offsets[2][1]
    set_free_offset(data, end_of_third)  # the header was last flushed after record 3
    assert ids(evtx.iter_records(bytes(data))) == [(1, False), (2, False), (3, False)]
    struct.pack_into("<I", data, 120, 1)  # file flags: dirty
    assert ids(evtx.iter_records(bytes(data))) == [(i, False) for i in range(1, 6)]


def test_records_in_slack_space_are_recovered_on_request():
    data = bytearray(build_evtx(events(5)))
    offsets = record_offsets(data)
    set_free_offset(data, offsets[2][0] + offsets[2][1])  # records 4 and 5 now sit in slack space
    data[CHUNK + offsets[3][0]] = 0  # record 4's signature is overwritten
    # Bytes that look like a record signature but aren't one are ignored, not reported as broken.
    junk = CHUNK + 60000
    data[junk:junk + 12] = b"\x2a\x2a\x00\x00" + struct.pack("<I", 40) + b"\x00\x00\x00\x00"
    assert ids(evtx.iter_records(bytes(data))) == [(1, False), (2, False), (3, False)]
    got = list(evtx.iter_records(bytes(data), recover=True))
    assert ids(got) == [(1, False), (2, False), (3, False), (5, True)]
    assert got[-1]["user"] == "user4" and got[-1]["event_id"] == 4625


def test_chunk_with_a_wiped_signature_is_carved_when_recovering(tmp_path):
    data = bytearray(build_evtx(events(3)))
    data[CHUNK:CHUNK + 8] = b"\x00" * 8
    assert list(evtx.iter_records(bytes(data))) == []
    assert ids(evtx.iter_records(bytes(data), recover=True)) == [(1, True), (2, True), (3, True)]
    p = tmp_path / "wiped.evtx"
    p.write_bytes(bytes(data))
    out = run_tb(f'import evtx\nsay(len(evtx.read("{tb_path(p)}")))\n'
                 f'let rs = evtx.read("{tb_path(p)}", {{"recover": true}})\n'
                 'say(len(rs), rs[0].recovered, rs[2].user)')
    assert out == ["0", "3 true user2"]


def patch_after_name(data: bytearray, name: str, skip: int, new: int):
    """Overwrite the byte `skip` bytes after the inline name `name` (first occurrence: the template)."""
    at = bytes(data).index(name.encode("utf-16-le") + b"\x00\x00", CHUNK) + 2 * len(name) + 2
    data[at + skip] = new


def test_unknown_token_in_element_content_is_skipped_to_the_element_end():
    data = bytearray(build_evtx(events(3)))
    # <Computer> in the shared template: closer 0x02, then the substitution token 0x0d -> unknown 0x3f.
    patch_after_name(data, "Computer", 1, 0x3F)
    got = list(evtx.iter_records(bytes(data)))
    assert [r["record_id"] for r in got] == [1, 2, 3]
    assert all(r.get("partial") and not r.get("broken") for r in got)
    # Everything before and after the damaged element is still there.
    assert got[1]["computer"] in (None, "") and got[1]["channel"] == "Security"
    assert got[1]["user"] == "user1" and got[1]["event_id"] == 4625 and got[1]["user_sid"] is None


def test_unknown_inline_value_type_in_an_attribute_is_skipped():
    data = bytearray(build_evtx(events(2)))
    # <Data Name="TargetUserName">: the attribute's inline value has type 0x01 (text) -> unknown 0x2f.
    at = bytes(data).index("TargetUserName".encode("utf-16-le"), CHUNK)
    assert data[at - 4:at] == b"\x05\x01\x0e\x00"  # inline value token, type, character count, then the text
    data[at - 3] = 0x2F
    got = list(evtx.iter_records(bytes(data)))
    assert len(got) == 2 and got[0]["partial"] and got[0]["LogonType"] == 3
    assert "TargetUserName" not in got[0]["event_data"]  # its Data element lost its Name


def test_self_referencing_template_is_a_friendly_error_not_a_crash():
    parser = evtx.ChunkParser(b"\x00" * 512)
    parser.depth = evtx.MAX_DEPTH
    try:
        parser.enter()
    except evtx.EvtxFormat as err:
        assert "nest deeper" in str(err)
    else:
        raise AssertionError("expected EvtxFormat")


def test_task_and_opcode_names():
    r = next(evtx.iter_records(build_evtx(events(1))))
    assert (r["task"], r["task_name"], r["opcode"], r["opcode_name"]) == (12544, "Logon", 0, "Info")
    # A rendered (forwarded) event carries the names Windows showed: they win.
    ev = evtx.Element("Event")
    system = evtx.Element("System")
    for name, value in (("EventID", 7036), ("Task", 0), ("Opcode", 0), ("Channel", "System"),
                        ("EventRecordID", 9), ("Computer", "SRV")):
        el = evtx.Element(name)
        el.children.append(value)
        system.children.append(el)
    provider = evtx.Element("Provider")
    provider.attrs["Name"] = "Service Control Manager"
    system.children.insert(0, provider)
    rendering = evtx.Element("RenderingInfo")
    for name, value in (("Task", "None"), ("Opcode", "Info"), ("Level", "Information"),
                        ("Message", "The Print Spooler service entered the running state.")):
        el = evtx.Element(name)
        el.children.append(value)
        rendering.children.append(el)
    ev.children += [system, rendering]
    r = evtx.build_record(ev, 9, 0, 0, False)
    assert (r["task_name"], r["opcode_name"], r["level_name"]) == ("None", "Info", "Information")
    assert r["message"] == "The Print Spooler service entered the running state."


def mutate(rng, data: bytes) -> bytes:
    out = bytearray(data)
    kind = rng.randrange(6)
    if kind == 0:  # bit flips anywhere
        for _ in range(rng.randint(1, 40)):
            i = rng.randrange(len(out))
            out[i] ^= 1 << rng.randrange(8)
    elif kind == 1:  # bit flips in the records area of the first chunk
        for _ in range(rng.randint(1, 20)):
            i = rng.randrange(CHUNK + 512, len(out))
            out[i] ^= 1 << rng.randrange(8)
    elif kind == 2:  # truncation
        del out[rng.randrange(len(out)):]
    elif kind == 3:  # chunk header damage
        for _ in range(rng.randint(1, 16)):
            out[CHUNK + rng.randrange(512)] = rng.randrange(256)
    elif kind == 4:  # a run of 0x00 / 0xff / random bytes
        start = rng.randrange(len(out))
        fill = rng.choice([b"\x00", b"\xff", None])
        n = rng.randint(1, 64)
        out[start:start + n] = fill * n if fill else bytes(rng.randrange(256) for _ in range(n))
    else:  # file header damage
        for _ in range(rng.randint(1, 8)):
            out[rng.randrange(128)] = rng.randrange(256)
    return bytes(out)


def test_fuzz_corrupted_files_give_friendly_errors_or_partial_results():
    base = build_evtx(events(3) + [security(4688, T + 9, "WS01", {
        "NewProcessName": "C:\\Windows\\System32\\cmd.exe", "CommandLine": "cmd /c whoami", "B": Value.boolean(True),
        "G": Value.guid("{11111111-2222-3333-4444-555555555555}"), "Bin": Value.binary(b"\x01\xff"),
        "Many": Value.strings(["a", "b"]), "When": Value.time(T), "H": Value.hex64(0x3E7)}, record_id=7)])
    module = evtx.load(None)
    parse = module.fields["parse"].fn
    rng = random.Random(24)
    faulthandler.dump_traceback_later(120, exit=True)  # a hang fails the run loudly instead of stalling CI
    try:
        slowest = 0.0
        outcomes = {"records": 0, "friendly": 0}
        for i in range(2000):
            data = mutate(rng, base)
            opts = {"recover": i % 2 == 1, "strict": i % 7 == 0, "xml": i % 5 == 0}
            started = time.perf_counter()
            try:
                records = parse(1, data, opts)
                assert isinstance(records, list) and all(isinstance(r, dict) for r in records)
                outcomes["records"] += 1
            except tb.TBError as err:
                assert "EVTX" in str(err), str(err)
                outcomes["friendly"] += 1
            try:
                evtx.file_info(data)  # evtx.info(): EvtxFormat and struct.error become friendly errors
            except (evtx.EvtxFormat, struct.error):
                pass
            slowest = max(slowest, time.perf_counter() - started)
    finally:
        faulthandler.cancel_dump_traceback_later()
    assert outcomes["records"] > 700 and outcomes["friendly"] > 70, outcomes
    assert slowest < 5.0, slowest  # generous: CI machines are slow and shared
