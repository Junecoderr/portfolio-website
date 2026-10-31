"""evtx library against real Windows logs: export this machine's own event logs with
`wevtutil epl`, render the same file with `wevtutil qe /lf:true /f:xml`, and check that
TB's reader gives the same event ID, provider, time, record ID, computer, channel and
EventData/UserData values for a sample of records in every log.

Runs only on Windows (CI: windows-latest, where the runner is an administrator, which
the Security log needs). Sysmon is compared when it is installed (tb-ci.yml installs
it from Microsoft's Sysinternals download); otherwise that case is skipped and says so.
No event logs are downloaded or committed: every fixture is made on the machine itself.
"""

import calendar
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest

from tblib import evtx

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="needs Windows: exports this machine's own event logs with wevtutil")

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
SAMPLE = 5000         # records compared per log: every record wevtutil renders, so rare events (e.g. boot-time 6009) are always checked
MAX_RENDERED = 5000   # records wevtutil renders per log (oldest first)
# (channel, must have records: fail rather than skip when it is empty or can't be exported)
CHANNELS = [
    ("Security", True),
    ("System", True),
    ("Application", False),
    ("Microsoft-Windows-PowerShell/Operational", False),
    ("Microsoft-Windows-Sysmon/Operational", False),
]


def run(args, timeout=300):
    """Run a command through cmd after `chcp 65001`, so wevtutil writes UTF-8 whatever the
    console's code page (`&`, not `&&`: the command still runs if chcp can't switch). The
    command line is passed as one string so cmd keeps the quotes around paths."""
    return subprocess.run("cmd /d /c chcp 65001 >nul & " + subprocess.list2cmdline(args),
                          capture_output=True, timeout=timeout)


def text(proc):
    return proc.stdout.decode("utf-8", "replace")


def make_some_events():
    """Best effort: make sure Application and PowerShell/Operational have records to compare."""
    run(["eventcreate", "/T", "INFORMATION", "/ID", "100", "/L", "APPLICATION", "/SO", "TBEvtxTest",
         "/D", "TB evtx test: real-log comparison"], timeout=60)
    run(["powershell", "-NoProfile", "-Command", "Write-Output 'TB evtx test'"], timeout=120)


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    make_some_events()
    return tmp_path_factory.mktemp("winlogs")


def local(tag):
    return tag.split("}", 1)[-1]


def norm(value) -> str:
    """A value as wevtutil writes it, roughly: text with line ends and outer spaces evened out."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "\n".join(norm(v) for v in value)
    return str(value).replace("\r\n", "\n").replace("\r", "\n").rstrip("\x00").strip()


HEXISH = re.compile(r"[{}0-9a-fA-FxX-]+")
TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z?$")


def epoch(text_value):
    m = TIME.match(text_value or "")
    if not m:
        return None
    whole = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
    return whole + (int(m.group(2)) / 10 ** len(m.group(2)) if m.group(2) else 0.0)


def same(tb_value, win_text) -> bool:
    a, b = norm(tb_value), norm(win_text)
    if a == b:
        return True
    if "\ufffd" in b:  # wevtutil's text lost a character in the console encoding: nothing to compare
        return True
    if a.casefold() == b.casefold() and HEXISH.fullmatch(b):  # GUIDs and binary: Windows writes upper case
        return True
    ta, tb_ = epoch(a), epoch(b)  # FILETIME values: TB keeps microseconds, Windows 100 ns ticks
    if ta is not None and tb_ is not None and abs(ta - tb_) < 2e-6:
        return True
    try:  # floats: formatting differs
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    except ValueError:
        return False


def rendered_events(path):
    proc = run(["wevtutil", "qe", str(path), "/lf:true", "/f:xml", f"/c:{MAX_RENDERED}"])
    assert proc.returncode == 0, f"wevtutil qe failed: {text(proc)} {proc.stderr.decode('utf-8', 'replace')}"
    events, unreadable = [], 0
    for chunk in re.findall(r"<Event[ >].*?</Event>", text(proc), re.S):
        # Control characters come out as &#x1; and the like, which XML 1.0 doesn't allow.
        chunk = re.sub(r"&#x0*([0-8bBcCeEfF]|1[0-9a-fA-F]);", "", chunk)
        try:
            events.append(ET.fromstring(chunk))
        except ET.ParseError:
            unreadable += 1
    return events, unreadable


def windows_fields(ev):
    system = ev.find(NS + "System")

    def find(name):
        return system.find(NS + name)

    out = {
        "event_id": int(find("EventID").text),
        "provider": find("Provider").get("Name"),
        "time": find("TimeCreated").get("SystemTime"),
        "record_id": int(find("EventRecordID").text),
        "computer": find("Computer").text,
        "channel": find("Channel").text,
        "data": {},
        "anonymous": [],
    }
    data = ev.find(NS + "EventData")
    if data is not None:
        for d in data:
            if local(d.tag) != "Data":
                continue
            if d.get("Name") is None:
                out["anonymous"].append(d.text)
            else:
                out["data"][d.get("Name")] = d.text
    user = ev.find(NS + "UserData")
    if user is not None:
        for wrapper in user:
            for leaf in wrapper:
                if len(leaf) == 0:
                    out["data"][local(leaf.tag)] = leaf.text
    return out


def compare(channel, path):
    """Mismatch descriptions, and how many records and fields were compared."""
    events, unreadable = rendered_events(path)
    assert unreadable <= max(2, len(events) // 20), f"{channel}: wevtutil gave {unreadable} unreadable events"
    if not events:
        return None, 0, 0
    step = max(1, len(events) // SAMPLE)
    wanted = {}
    for ev in events[::step][:SAMPLE]:
        w = windows_fields(ev)
        wanted[w["record_id"]] = w
    got, seen, first, tb_error = {}, 0, [], None
    try:
        with open(path, "rb") as f:
            for r in evtx.iter_records(f, want_xml=True):
                seen += 1
                if len(first) < 3:
                    first.append({k: r.get(k) for k in ("record_id", "event_id", "provider", "channel")})
                if r.get("record_id") in wanted:
                    got[r["record_id"]] = r
                    if len(got) == len(wanted):
                        break
    except Exception as err:  # reported below, so CI shows why TB found nothing
        tb_error = f"{type(err).__name__}: {err}"
    if not got:
        import os
        with open(path, "rb") as f:
            raw = f.read(4096 + 512 + 64)
        import struct
        hdr = struct.unpack_from("<QQQ", raw, 8)  # file header: first chunk, last chunk, next record id
        ch = evtx.chunk_header(raw[4096:4096 + 65536]) if len(raw) >= 4096 + 128 else {}
        print(f"{channel}: file header first/last chunk, next record id: {hdr}; chunk 0 header: {ch}; "
              f"first record header bytes: {raw[4096 + 512:4096 + 512 + 32].hex()}")
        print(f"{channel}: TB read {seen} records (first: {first}); error: {tb_error}; "
              f"file size {os.path.getsize(path)}; wevtutil ids {sorted(wanted)[:3]}...")
    problems, fields = [], 0

    def check(rid, what, tb_value, win_value):
        nonlocal fields
        fields += 1
        if not same(tb_value, win_value):
            problems.append(f"{channel} record {rid} (event {wanted[rid]['event_id']}): {what}: "
                            f"TB {norm(tb_value)!r} vs wevtutil {norm(win_value)!r}"
                            + (f" [TB value {tb_value!r}; TB xml {str(got[rid].get('xml'))[:1500]!r}]"
                               if what.startswith("EventData") else ""))

    for rid, w in wanted.items():
        r = got.get(rid)
        if r is None:
            problems.append(f"{channel} record {rid}: wevtutil shows it, TB doesn't")
            continue
        if r.get("broken"):
            problems.append(f"{channel} record {rid}: TB couldn't decode it: {r.get('message')}")
            continue
        for key in ("event_id", "provider", "record_id", "computer", "channel"):
            check(rid, key, r.get(key), w[key])
        fields += 1
        when = epoch(w["time"])
        if when is None or r.get("time") is None or abs(r["time"] - when) > 2e-6:
            problems.append(f"{channel} record {rid}: time: TB {r.get('time')} vs wevtutil {w['time']}")
        data = r.get("event_data") or {}
        for name, value in w["data"].items():
            check(rid, f"EventData {name}", data.get(name), value)
        if w["anonymous"]:
            mine = data.get("Data")
            mine = mine if isinstance(mine, list) and len(w["anonymous"]) > 1 else [mine]
            # Compare item by item: joining and trimming would hide (or invent) empty
            # strings at either end of the list.
            check(rid, "EventData (unnamed Data)", [norm(v) for v in mine], [norm(v) for v in w["anonymous"]])
    return problems, len(wanted), fields


@pytest.mark.parametrize("channel,required", CHANNELS, ids=[c for c, _ in CHANNELS])
def test_records_match_wevtutil(channel, required, exported):
    if run(["wevtutil", "gli", channel], timeout=60).returncode != 0:
        if "Sysmon" in channel:
            pytest.skip("Sysmon is not installed on this machine, so there is no Sysmon log to compare")
        if required:
            pytest.fail(f"the {channel} log doesn't exist here")
        pytest.skip(f"no {channel} log on this machine")
    path = exported / (channel.replace("/", "_") + ".evtx")
    proc = run(["wevtutil", "epl", channel, str(path)])
    if proc.returncode != 0:
        why = (text(proc) + proc.stderr.decode("utf-8", "replace")).strip()
        message = f"wevtutil epl {channel} failed (the Security log needs an administrator): {why}"
        if required:
            pytest.fail(message)
        pytest.skip(message)
    problems, records, fields = compare(channel, path)
    if not records:
        if required:
            pytest.fail(f"the {channel} log has no records to compare")
        pytest.skip(f"the {channel} log has no records")
    print(f"{channel}: compared {records} records, {fields} fields, {len(problems)} differences")
    assert not problems, (f"{len(problems)} differences between TB and wevtutil in {channel} "
                          f"({records} records, {fields} fields compared):\n" + "\n".join(problems[:40]))
