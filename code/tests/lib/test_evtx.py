"""evtx library: binary EVTX parsing (chunks, records, binary XML templates and
substitutions) checked against files made by tests/lib/evtxwriter.py."""

import struct

from conftest import ROOT, run_tb, tb_error
from evtxwriter import (SECURITY_GUID, SID_SYSTEM, SID_TANISHA, Value, build_evtx, sample_evtx_bytes,
                        security)

SAMPLE = (ROOT / "apps" / "data" / "sample.evtx").as_posix()
T = 1773302400.0  # 2026-03-12 08:00:00 UTC


def tb_path(p):
    return str(p).replace("\\", "/")


def write(tmp_path, events, name="x.evtx"):
    path = tmp_path / name
    path.write_bytes(build_evtx(events))
    return tb_path(path)


def failed_logon(i, user="admin", ip="203.0.113.7"):
    return security(4625, T + i, "WS01.corp.example", {
        "TargetUserName": user, "TargetDomainName": "CORP", "LogonType": Value.u32(3),
        "IpAddress": ip, "IpPort": "50123", "Status": Value.hex32(0xC000006D),
        "SubjectUserSid": Value.sid(SID_SYSTEM)}, success=False, record_id=i + 1, task=12544)


def test_sample_file_matches_the_writer():
    assert (ROOT / "apps" / "data" / "sample.evtx").read_bytes() == sample_evtx_bytes()


def test_fields_match_logs_names_and_mapping(tmp_path):
    path = write(tmp_path, [failed_logon(0)])
    out = run_tb(f'import evtx\nlet r = evtx.read("{path}")[0]\n'
                 'say(r.event_id, r.EventID, r.action, r.success, r.user, r.ip)\n'
                 'say(r.description)\n'
                 'say(r.computer, r.channel, r.provider, r.record_id, r.format, r.source)\n'
                 'say(r.when, r.TargetUserName, r.LogonType, r.Status, r.event_data.IpPort)\n'
                 'say(r.keywords, r.level_name, r.provider_guid)')
    assert out[0] == "4625 4625 failed_login false admin 203.0.113.7"
    assert out[1] == "An account failed to log on"
    assert out[2] == "WS01.corp.example Security Microsoft-Windows-Security-Auditing 1 windows evtx"
    assert out[3].endswith(" admin 3 0xc000006d 50123")
    assert out[4] == f'["Audit Failure"] LogAlways {SECURITY_GUID}'


def test_value_types(tmp_path):
    ev = security(4688, T, "WS01", {
        "S": "text", "U8": Value.u8(7), "U16": Value.u16(300), "U32": Value.u32(70000), "I32": Value.i32(-5),
        "U64": Value.u64(2 ** 40), "B": Value.boolean(True), "G": Value.guid("{11111111-2222-3333-4444-555555555555}"),
        "Sid": Value.sid(SID_TANISHA), "H64": Value.hex64(0x3E7), "Bin": Value.binary(b"\x01\xff"),
        "Many": Value.strings(["a", "b"]), "Nothing": Value.null(), "When": Value.time(T + 0.5)}, record_id=1)
    path = write(tmp_path, [ev])
    out = run_tb(f'import evtx\nlet d = evtx.read("{path}")[0].event_data\n'
                 'for k in ["S", "U8", "U16", "U32", "I32", "U64", "B", "G", "Sid", "H64", "Bin", "Many", "Nothing", "When"] {\n'
                 '  say(k + "=" + str(d.get(k, "MISSING")))\n}')
    got = dict(line.split("=", 1) for line in out)
    assert got["S"] == "text" and got["U8"] == "7" and got["U16"] == "300" and got["U32"] == "70000"
    assert got["I32"] == "-5" and got["U64"] == str(2 ** 40) and got["B"] == "true"
    assert got["G"] == "{11111111-2222-3333-4444-555555555555}"
    assert got["Sid"] == SID_TANISHA and got["H64"] == "0x3e7" and got["Bin"].lower() == "01ff"
    assert got["Many"] in ('["a", "b"]', "a, b", "a\nb") or "a" in got["Many"]
    assert got["Nothing"] in ("nil", "")
    assert got["When"].startswith("2026-03-12T08:00:00.5")


def test_many_events_span_chunks_and_templates_are_reused(tmp_path):
    events = [failed_logon(i, user=f"user{i % 7}") for i in range(400)]
    path = write(tmp_path, events)
    out = run_tb(f'import evtx\nlet info = evtx.info("{path}")\n'
                 'say(info.chunk_count > 1, info.records, info.checksums_ok, info.dirty)\n'
                 f'let rs = evtx.read("{path}")\nsay(len(rs), rs[399].record_id, rs[399].user)\n'
                 'say(rs.map(fn(r) { return r.chunk }).unique().len() > 1)\n'
                 f'say(len(evtx.read("{path}", {{"limit": 5}})))')
    assert out == ["true 400 true false", "400 400 user0", "true", "5"]


def test_each_summary_xml_and_json(tmp_path):
    path = write(tmp_path, [failed_logon(i) for i in range(5)])
    out = run_tb(f'import evtx\nlet n = 0\nsay(evtx.each("{path}", fn(r) {{ n += 1\n return n < 3 }}) == n)\nsay(n)\n'
                 f'let s = evtx.summary("{path}")\nsay(s.records, s.failures, s.event_ids, s.ips, s.checksums_ok)\n'
                 f'let r = evtx.read("{path}", {{"xml": true}})[0]\nsay(r.xml.starts_with("<Event"), r.xml.contains("<Data Name=\\"TargetUserName\\">admin</Data>"))\n'
                 'import json\nlet j = json.parse(evtx.to_json(r))\nsay(j.event_id, j.has("raw"))\n'
                 f'say(evtx.is_evtx("{path}"), evtx.levels[4])')
    assert out == ["true", "3", '5 5 {4625: 5} {"203.0.113.7": 5} true', "true true", "4625 false", "true Information"]


def test_sample_story():
    out = run_tb(f'import evtx\nlet rs = evtx.read("{SAMPLE}")\nsay(len(rs))\n'
                 'let ps = rs.filter(fn(r) { return r.event_id == 4688 and r.get("CommandLine", "").contains("-enc") })\n'
                 'say(len(ps))\n'
                 'say(rs.filter(fn(r) { return r.event_id == 1102 }).len())\n'
                 'say(rs.filter(fn(r) { return r.provider == "Microsoft-Windows-Sysmon" }).len())')
    assert out == ["24", "1", "1", "1"]


def test_logs_read_delegates_to_evtx(tmp_path):
    path = write(tmp_path, [failed_logon(i) for i in range(3)])
    out = run_tb(f'import logs\nlet rs = logs.read("{path}")\nsay(len(rs), rs[0].action, rs[0].source)\n'
                 f'say(len(logs.read("{path}", "evtx", {{"limit": 2}})))\n'
                 f'say(logs.each("{path}", fn(r) {{ return true }}, "evtx"))')
    assert out == ["3 failed_login evtx", "2", "3"]
    assert "binary files" in str(tb_error('import logs\nlogs.parse("x", "evtx")'))
    text = tmp_path / "a.log"
    text.write_text("hello\n")
    assert "isn't an EVTX log" in str(tb_error(f'import logs\nlogs.read("{tb_path(text)}", "evtx")'))


def test_damage_is_reported(tmp_path):
    good = bytearray(build_evtx([failed_logon(i) for i in range(3)]))
    # Flip a byte in the record data: the chunk's records checksum no longer matches.
    bad = bytearray(good)
    bad[4096 + 512 + 40] ^= 0xFF
    p = tmp_path / "bad.evtx"
    p.write_bytes(bytes(bad))
    out = run_tb(f'import evtx\nlet info = evtx.info("{tb_path(p)}")\nsay(info.checksums_ok)\n'
                 f'say(len(evtx.read("{tb_path(p)}")))')
    assert out[0] == "false"
    # Mark the file dirty (flags bit 0) and check it is reported.
    dirty = bytearray(good)
    struct.pack_into("<I", dirty, 120, 1)
    p2 = tmp_path / "dirty.evtx"
    p2.write_bytes(bytes(dirty))
    assert run_tb(f'import evtx\nsay(evtx.info("{tb_path(p2)}").dirty)') == ["true"]


def test_friendly_errors(tmp_path):
    assert "I couldn't find" in str(tb_error('import evtx\nevtx.read("missing.evtx")'))
    text = tmp_path / "a.txt"
    text.write_text("not a log")
    assert "isn't an EVTX log" in str(tb_error(f'import evtx\nevtx.read("{tb_path(text)}")'))
    assert "isn't an EVTX log" in str(tb_error(f'import evtx\nevtx.info("{tb_path(text)}")'))
    assert "evtx.read() needs" in str(tb_error('import evtx\nevtx.read(5)'))
    assert run_tb(f'import evtx\nsay(evtx.is_evtx("{tb_path(text)}"))') == ["false"]
