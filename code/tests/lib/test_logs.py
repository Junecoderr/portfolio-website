import datetime as dt
import json

import pytest

import tb
from conftest import run_tb, tb_error
from tblib.logs import LineParser, guess_year, iter_text, parse_time, sniff_text


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def local(*parts):
    return dt.datetime(*parts).timestamp()


def parse(line, fmt="auto", year=None):
    return LineParser(fmt, year).parse(line)


def records(text, fmt="auto", year=None):
    return list(iter_text(text, fmt, 1, year))


# -- time ------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2024-03-10T09:15:02Z", dt.datetime(2024, 3, 10, 9, 15, 2, tzinfo=dt.timezone.utc).timestamp()),
    ("2024-03-10T09:15:02.1234567+02:00", dt.datetime(2024, 3, 10, 7, 15, 2, 123456, tzinfo=dt.timezone.utc).timestamp()),
    ("2024-03-10 09:15:02", local(2024, 3, 10, 9, 15, 2)),
    ("2024/03/10 09:15", local(2024, 3, 10, 9, 15)),
    ("10/Mar/2024:09:15:02 +0000", dt.datetime(2024, 3, 10, 9, 15, 2, tzinfo=dt.timezone.utc).timestamp()),
    ("3/10/2024 9:15:02 PM", local(2024, 3, 10, 21, 15, 2)),
    ("3/10/2024 12:05:00 AM", local(2024, 3, 10, 0, 5)),
    ("25/12/2024 10:00", local(2024, 12, 25, 10, 0)),
    ("/Date(1710062102000)/", 1710062102),
    ("\\/Date(1710062102500)\\/", 1710062102.5),
    ("1710062102", 1710062102),
    ("1710062102000", 1710062102),
])
def test_parse_time_formats(text, expected):
    assert parse_time(text)[0] == pytest.approx(expected)


def test_parse_time_rejects_junk():
    for text in ["", "hello", "2024-13-40 99:99:99", "Foo 12 10:00:00", None, True, [1]]:
        assert parse_time(text) == (None, None)


def test_syslog_year_is_guessed_not_in_the_future():
    now = dt.datetime(2026, 1, 5, 12, 0)
    assert guess_year(12, 30, now) == 2025   # December logs read in January
    assert guess_year(1, 5, now) == 2026
    assert guess_year(1, 6, now) == 2026     # tomorrow is fine (clock skew)
    assert guess_year(2, 29, dt.datetime(2026, 3, 1)) == 2026  # invalid date: keep the year


def test_parse_time_from_tb():
    src = 'import logs\nsay(logs.parse_time("2024-03-10T09:15:02Z"), logs.parse_time("nope"), logs.parse_time(1710062102000))\nsay(logs.parse_time("Mar 10 09:15:02", 2023) == logs.parse_time("2023-03-10 09:15:02"))'
    assert run_tb(src) == ["1710062102 nil 1710062102", "true"]


# -- syslog ----------------------------------------------------------------------

def test_rfc3164_syslog():
    r = parse("<38>Mar  3 09:15:02 web01 cron[123]: (root) CMD (run-parts)", "syslog", 2024)
    assert r["format"] == "syslog"
    assert r["time"] == local(2024, 3, 3, 9, 15, 2) and r["when"] == "2024-03-03 09:15:02"
    assert (r["host"], r["program"], r["pid"]) == ("web01", "cron", 123)
    assert r["message"] == "(root) CMD (run-parts)"
    assert (r["facility"], r["severity"]) == (4, "info")
    assert r["raw"].startswith("<38>Mar  3")


def test_syslog_without_pid_and_without_tag():
    assert parse("Mar 10 10:00:00 host kernel: boom", "syslog", 2024)["program"] == "kernel"
    r = parse("Mar 10 10:00:00 host just some text", "syslog", 2024)
    assert r["program"] is None and r["message"] == "just some text"


def test_syslog_year_rolls_over_in_a_file():
    text = "Dec 31 23:59:58 h a: one\nJan  1 00:00:02 h a: two\n"
    first, second = records(text, "syslog", 2024)
    assert first["when"] == "2024-12-31 23:59:58" and second["when"] == "2025-01-01 00:00:02"


def test_iso_and_rfc5424_syslog():
    r = parse("2024-03-10T09:15:02.123456+00:00 web01 sshd[99]: hello there", "syslog")
    assert r["time"] == pytest.approx(1710062102.123456) and r["program"] == "sshd" and r["pid"] == 99
    r = parse("<34>1 2024-03-10T09:15:02Z mymachine.example.com su - ID47 - 'su root' failed for lonvick", "syslog")
    assert (r["host"], r["program"], r["pid"], r["severity"]) == ("mymachine.example.com", "su", None, "critical")
    assert r["message"] == "'su root' failed for lonvick"


def test_ufw_firewall_fields():
    line = ("Mar 12 10:39:30 web01 kernel: [715305.143690] [UFW BLOCK] IN=eth0 OUT= MAC=00:16 SRC=198.51.100.77 "
            "DST=192.0.2.5 LEN=44 PROTO=TCP SPT=61234 DPT=3389 WINDOW=1024 SYN URGP=0")
    r = parse(line, "syslog", 2026)
    assert r["message"].startswith("[UFW BLOCK]")
    assert (r["action"], r["ip"], r["src_ip"], r["dst_ip"], r["dst_port"], r["src_port"], r["proto"]) == (
        "firewall_block", "198.51.100.77", "198.51.100.77", "192.0.2.5", 3389, 61234, "TCP")


def test_unparseable_syslog_line_becomes_text():
    r = parse("this is not syslog", "syslog")
    assert r["format"] == "text" and r["time"] is None and r["message"] == "this is not syslog"


# -- auth.log ----------------------------------------------------------------------

AUTH_CASES = [
    ("sshd[1]: Failed password for root from 203.0.113.42 port 50611 ssh2",
     {"action": "failed_login", "success": False, "user": "root", "ip": "203.0.113.42", "port": 50611, "method": "password"}),
    ("sshd[1]: Failed password for invalid user admin from 203.0.113.42 port 1 ssh2",
     {"action": "failed_login", "user": "admin", "invalid_user": True}),
    ("sshd[1]: Failed publickey for git from 2001:db8::5 port 22 ssh2: RSA SHA256:x",
     {"action": "failed_login", "user": "git", "ip": "2001:db8::5", "method": "publickey"}),
    ("sshd[1]: Accepted password for deploy from 203.0.113.42 port 52144 ssh2",
     {"action": "login", "success": True, "user": "deploy", "ip": "203.0.113.42"}),
    ("sshd[1]: Accepted publickey for alice from 192.0.2.10 port 50412 ssh2: ED25519 SHA256:abc",
     {"action": "login", "success": True, "method": "publickey"}),
    ("sshd[1]: Invalid user oracle from 203.0.113.42 port 41228",
     {"action": "invalid_user", "success": False, "user": "oracle", "port": 41228}),
    ("sshd[1]: Invalid user  from 203.0.113.42 port 41228",
     {"action": "invalid_user", "user": None}),
    ("sshd[1]: pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost=203.0.113.42  user=root",
     {"action": "auth_failure", "success": False, "user": "root", "ip": "203.0.113.42", "service": "sshd"}),
    ("sshd[1]: Connection closed by authenticating user root 203.0.113.42 port 56627 [preauth]",
     {"action": "disconnect", "success": None, "user": "root", "ip": "203.0.113.42"}),
    ("sshd[1]: Did not receive identification string from 198.51.100.77 port 61000",
     {"action": "probe", "ip": "198.51.100.77"}),
    ("sudo:    alice : TTY=pts/0 ; PWD=/home/alice ; USER=root ; COMMAND=/usr/bin/apt update",
     {"action": "sudo", "success": True, "user": "alice", "target_user": "root", "command": "/usr/bin/apt update",
      "tty": "pts/0", "pwd": "/home/alice"}),
    ("sudo:      bob : 3 incorrect password attempts ; TTY=pts/2 ; PWD=/home/bob ; USER=root ; COMMAND=/bin/bash",
     {"action": "sudo_failed", "success": False, "user": "bob", "attempts": 3, "command": "/bin/bash"}),
    ("sudo:      eve : user NOT in sudoers ; TTY=pts/2 ; PWD=/home/eve ; USER=root ; COMMAND=/bin/sh",
     {"action": "sudo_denied", "success": False, "user": "eve"}),
    ("useradd[2]: new user: name=sysupdate, UID=1002, GID=1002, home=/home/sysupdate, shell=/bin/bash",
     {"action": "user_created", "user": "sysupdate"}),
    ("usermod[2]: add 'sysupdate' to group 'sudo'", {"action": "group_member_added", "user": "sysupdate", "group": "sudo"}),
    ("passwd[2]: pam_unix(passwd:chauthtok): password changed for sysupdate", {"action": "password_changed", "user": "sysupdate"}),
    ("CRON[5]: pam_unix(cron:session): session opened for user root(uid=0) by (uid=0)",
     {"action": "session_open", "user": "root", "service": "cron"}),
    ("systemd-logind[612]: New session 41 of user alice.", {"action": "session_open", "user": "alice."}),
]


@pytest.mark.parametrize("message,expected", AUTH_CASES)
def test_auth_messages(message, expected):
    r = parse(f"Mar 12 09:14:05 web01 {message}", "auth", 2026)
    assert r["format"] == "auth"
    for key, value in expected.items():
        assert r.get(key) == value, (key, r)
    for key in ("user", "ip", "action", "success", "time", "message", "raw"):
        assert key in r


def test_auth_message_repeated_counts():
    r = parse("Mar 12 09:14:09 web01 sshd[9]: message repeated 2 times: [ Failed password for root from 203.0.113.42 port 1 ssh2]", "auth", 2026)
    assert (r["action"], r["user"], r["ip"], r["repeat"], r["success"]) == ("failed_login", "root", "203.0.113.42", 2, False)


def test_auth_other_lines_keep_syslog_fields():
    r = parse("Mar 12 09:14:09 web01 systemd[1]: Started Session 4.", "auth", 2026)
    assert r["format"] == "auth" and r["action"] is None and r["program"] == "systemd" and r["user"] is None


# -- apache / nginx -----------------------------------------------------------------

def test_apache_combined():
    line = ('198.51.100.77 - frank [12/Mar/2026:10:41:02 +0000] "GET /index.php?id=1%27 HTTP/1.1" 500 431 '
            '"https://www.example.com/" "sqlmap/1.7.2#stable (https://sqlmap.org)"')
    r = parse(line, "apache")
    assert r["format"] == "apache"
    assert r["time"] == dt.datetime(2026, 3, 12, 10, 41, 2, tzinfo=dt.timezone.utc).timestamp()
    assert r["when"] == "2026-03-12 10:41:02"
    assert (r["ip"], r["user"], r["method"], r["path"], r["protocol"]) == (
        "198.51.100.77", "frank", "GET", "/index.php?id=1%27", "HTTP/1.1")
    assert (r["status"], r["bytes"], r["referrer"], r["agent"]) == (
        500, 431, "https://www.example.com/", "sqlmap/1.7.2#stable (https://sqlmap.org)")
    assert r["message"] == "GET /index.php?id=1%27 HTTP/1.1"


def test_apache_common_format_dash_size_and_nginx_alias():
    r = parse('192.0.2.1 - - [10/Oct/2000:13:55:36 -0700] "POST /login HTTP/1.0" 401 -', "nginx")
    assert (r["status"], r["bytes"], r["referrer"], r["agent"], r["user"]) == (401, 0, None, None, None)
    assert r["time"] == dt.datetime(2000, 10, 10, 20, 55, 36, tzinfo=dt.timezone.utc).timestamp()


def test_apache_escaped_quotes_and_garbage_request():
    r = parse(r'192.0.2.1 - - [10/Oct/2000:13:55:36 +0000] "GET /a\"b HTTP/1.1" 404 1 "-" "x \"y\""', "apache")
    assert r["path"] == '/a"b' and r["agent"] == r'x \"y\"'
    r = parse(r'198.51.100.77 - - [12/Mar/2026:11:05:00 +0000] "\x16\x03\x01\x00" 400 226 "-" "-"', "apache")
    assert r["method"] is None and r["path"] is None and r["status"] == 400 and r["request"] == r"\x16\x03\x01\x00"


# -- JSON and CSV ------------------------------------------------------------------

def test_json_lines():
    text = ('{"timestamp": "2024-03-10T09:15:02Z", "msg": "login failed", "src_ip": "203.0.113.5", "username": "bob", "outcome": "failure"}\n'
            '{"time": 1710062103, "message": "ok", "ip": "192.0.2.1", "user": "amy", "n": 2.0, "geo": {"country": "NL"}}\n'
            "\n"
            "not json\n")
    a, b, c = records(text, "json")
    assert (a["time"], a["message"], a["ip"], a["user"], a["success"], a["format"]) == (
        1710062102, "login failed", "203.0.113.5", "bob", False, "json")
    assert (b["time"], b["message"], b["n"], b["geo"]) == (1710062103, "ok", 2, {"country": "NL"})
    assert c["format"] == "text"


def test_json_time_field_keeps_original_text():
    r = parse('{"time": "2024-03-10T09:15:02Z", "message": "x"}', "json")
    assert r["time"] == 1710062102 and r["time_text"] == "2024-03-10T09:15:02Z"


def test_json_document_array_and_wrapper(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([{"ts": 1710062102, "event": "a"}, {"ts": 1710062103, "event": "b"}], indent=2))
    (tmp_path / "b.json").write_text(json.dumps({"records": [{"message": "x"}, {"message": "y"}]}, indent=2))
    src = 'import logs\nlet a = logs.read("a.json")\nsay(len(a), a[0].message, a[1].time)\nsay(logs.read("b.json").map(fn(r) { return r.message }))'
    assert run_tb(src) == ["2 a 1710062103", '["x", "y"]']


def test_csv_log(tmp_path):
    (tmp_path / "fw.csv").write_text('Date,Source IP,Action,Message\n2024-03-10 09:15:02,203.0.113.5,deny,"blocked, twice"\n\n2024-03-10 09:15:03,192.0.2.1,allow,ok\n')
    src = 'import logs\nlet rs = logs.read("fw.csv")\nsay(len(rs), rs[0].format, rs[0]["Source IP"], rs[0].message, rs[0].when, rs[1].Action)'
    assert run_tb(src) == ["2 csv 203.0.113.5 blocked, twice 2024-03-10 09:15:02 allow"]
    assert parse("a,b,\"c,d\"", "csv")["fields"] == ["a", "b", "c,d"]


# -- Windows ---------------------------------------------------------------------

FAILED_4625 = ("An account failed to log on.\r\n\r\nSubject:\r\n\tSecurity ID:\t\tS-1-0-0\r\n\tAccount Name:\t\t-\r\n"
               "\tAccount Domain:\t\t-\r\n\r\nLogon Type:\t\t\t3\r\n\r\nAccount For Which Logon Failed:\r\n"
               "\tSecurity ID:\t\tS-1-0-0\r\n\tAccount Name:\t\tadministrator\r\n\tAccount Domain:\t\tCORP\r\n\r\n"
               "Failure Information:\r\n\tFailure Reason:\t\tUnknown user name or bad password.\r\n\tStatus:\t\t\t0xC000006D\r\n"
               "\tSub Status:\t\t0xC000006A\r\n\r\nNetwork Information:\r\n\tWorkstation Name:\tWIN-R4ND0M\r\n"
               "\tSource Network Address:\t203.0.113.99\r\n\tSource Port:\t\t0")


def test_windows_get_winevent_json_ps7():
    obj = [{"Id": 4625, "ProviderName": "Microsoft-Windows-Security-Auditing", "MachineName": "WS01",
            "TimeCreated": "2026-03-12T02:10:03.1234567+00:00", "KeywordsDisplayNames": ["Audit Failure"],
            "Message": FAILED_4625}]
    (r,) = records(json.dumps(obj, indent=2), "auto")
    assert r["format"] == "windows"
    assert r["time"] == pytest.approx(dt.datetime(2026, 3, 12, 2, 10, 3, 123456, tzinfo=dt.timezone.utc).timestamp())
    assert (r["event_id"], r["EventID"], r["action"], r["success"]) == (4625, 4625, "failed_login", False)
    assert (r["user"], r["domain"], r["ip"], r["port"], r["workstation"]) == ("administrator", "CORP", "203.0.113.99", 0, "WIN-R4ND0M")
    assert (r["logon_type"], r["logon_type_name"], r["status"], r["sub_status"]) == (3, "network", "0xC000006D", "0xC000006A")
    assert (r["TargetUserName"], r["IpAddress"], r["computer"]) == ("administrator", "203.0.113.99", "WS01")
    assert r["message"] == "An account failed to log on."
    assert r["failure_reason"] == "Unknown user name or bad password."


def test_windows_ps5_date_and_properties_fallback():
    props = [{"Value": v} for v in ["S-1-0-0", "-", "-", "0x0", "S-1-0-0", "bob", "CORP", "0xC000006D", "%%2313",
                                     "0xC000006A", 3, "NtLmSsp", "NTLM", "KALI", "-", "-", 0, "0x0", "-",
                                     "198.51.100.9", "51515"]]
    obj = {"Id": 4625, "TimeCreated": "/Date(1710062102000)/", "Properties": props, "Message": ""}
    r = parse(json.dumps(obj), "windows")
    assert r["time"] == 1710062102
    assert (r["user"], r["ip"], r["port"], r["logon_type"], r["workstation"]) == ("bob", "198.51.100.9", 51515, 3, "KALI")
    assert r["message"] == "An account failed to log on"


def test_windows_eventdata_style_jsonl():
    line = json.dumps({"EventID": 4688, "Computer": "WS02", "TimeCreated": {"SystemTime": "2026-03-12T02:14:46Z"},
                       "EventData": {"SubjectUserName": "backup", "NewProcessName": "C:\\Windows\\System32\\cmd.exe",
                                     "CommandLine": "cmd.exe /c whoami", "ParentProcessName": "C:\\Windows\\explorer.exe"}})
    r = records(line + "\n" + line + "\n", "auto")[0]
    assert r["format"] == "windows" and r["action"] == "process_created"
    assert (r["user"], r["process"], r["command_line"], r["Image"], r["ParentImage"], r["computer"]) == (
        "backup", "C:\\Windows\\System32\\cmd.exe", "cmd.exe /c whoami", "C:\\Windows\\System32\\cmd.exe",
        "C:\\Windows\\explorer.exe", "WS02")


def test_windows_4720_and_4740_users():
    created = ("A user account was created.\r\n\r\nSubject:\r\n\tAccount Name:\t\tadmin\r\n\tAccount Domain:\t\tCORP\r\n\r\n"
               "New Account:\r\n\tAccount Name:\t\thelpdesk2\r\n\tAccount Domain:\t\tCORP")
    r = parse(json.dumps({"Id": 4720, "Message": created}), "windows")
    assert (r["action"], r["user"], r["target_user"], r["subject_user"]) == ("user_created", "helpdesk2", "helpdesk2", "admin")
    locked = ("A user account was locked out.\r\n\r\nSubject:\r\n\tAccount Name:\t\tDC01$\r\n\r\n"
              "Account That Was Locked Out:\r\n\tAccount Name:\t\tsvc_sql\r\n\r\nAdditional Information:\r\n\tCaller Computer Name:\tWIN-R4ND0M")
    r = parse(json.dumps({"Id": 4740, "Message": locked}), "windows")
    assert (r["action"], r["user"], r["CallerComputerName"]) == ("account_locked", "svc_sql", "WIN-R4ND0M")


def test_event_viewer_csv_and_powershell_csv(tmp_path):
    msg = FAILED_4625.replace("\r\n", "\n")
    (tmp_path / "ev.csv").write_text(
        "Level,Date and Time,Source,Event ID,Task Category\n"
        f'Information,3/12/2026 2:06:28 PM,Microsoft-Windows-Security-Auditing,4625,Logon,"{msg}"\n')
    (tmp_path / "ps.csv").write_text(
        '#TYPE System.Diagnostics.Eventing.Reader.EventLogRecord\n"Message","Id","TimeCreated","MachineName"\n'
        f'"{msg}","4625","3/12/2026 2:06:28 PM","WS01"\n')
    src = '''import logs
for f in ["ev.csv", "ps.csv"] {
  let r = logs.read(f)[0]
  say(logs.sniff(f), r.format, r.event_id, r.user, r.ip, r.when, r.computer)
}'''
    assert run_tb(src) == ["windows windows 4625 administrator 203.0.113.99 2026-03-12 14:06:28 nil",
                           "windows windows 4625 administrator 203.0.113.99 2026-03-12 14:06:28 WS01"]


def test_utf16_powershell_export(tmp_path):
    data = json.dumps([{"Id": 4624, "Message": "An account was successfully logged on.\r\n\r\nNew Logon:\r\n\tAccount Name:\t\tamy"}])
    (tmp_path / "u16.json").write_bytes(b"\xff\xfe" + data.encode("utf-16-le"))
    assert run_tb('import logs\nlet r = logs.read("u16.json")[0]\nsay(r.action, r.user, r.success)') == ["login amy true"]


# -- auto, files and the TB functions --------------------------------------------------

@pytest.mark.parametrize("name,fmt,count", [
    ("auth.log", "auth", 101), ("access.log", "apache", 114), ("firewall.log", "syslog", 39),
    ("windows_security.json", "windows", 31), ("windows_security.csv", "windows", 9),
])
def test_sample_logs_sniff_and_read(root, name, fmt, count):
    path = str(root / "apps" / "data" / name)
    out = run_tb(f'import logs\nlet rs = logs.read("{path}")\nsay(logs.sniff("{path}"), len(rs), rs[0].format)')
    assert out == [f"{fmt} {count} {fmt}"]


def test_sniff_text_kinds():
    assert sniff_text("") == "text"
    assert sniff_text('{"a": 1}\n{"a": 2}\n') == "json"
    assert sniff_text('[\n {"Id": 4624}\n]') == "windows"
    assert sniff_text('{"EventID": 4624}\n') == "windows"
    assert sniff_text("a,b,c\n1,2,3\n") == "csv"
    assert sniff_text("Mar 10 10:00:00 h nginx[1]: x\n") == "syslog"
    assert sniff_text("Mar 10 10:00:00 h sshd[1]: x\n") == "auth"
    assert sniff_text("hello world\n") == "text"


def test_parse_line_auto_picks_per_line():
    assert parse('{"msg": "x"}')["format"] == "json"
    assert parse('{"EventID": 4625}')["format"] == "windows"
    assert parse('192.0.2.1 - - [10/Oct/2000:13:55:36 +0000] "GET / HTTP/1.1" 200 5')["format"] == "apache"
    assert parse("Mar 10 10:00:00 h sshd[1]: Failed password for x from 192.0.2.1 port 1 ssh2")["format"] == "auth"
    assert parse("Mar 10 10:00:00 h nginx: started")["format"] == "syslog"
    assert parse("2024-03-10 10:00:00 something happened")["time"] == local(2024, 3, 10, 10, 0)


def test_tb_parse_line_parse_limit_each_top(tmp_path):
    lines = "\n".join(f"Mar 12 09:{i // 60:02d}:{i % 60:02d} h sshd[1]: Failed password for root from 192.0.2.{i % 3} port 1 ssh2"
                      for i in range(300))
    (tmp_path / "big.log").write_text(lines + "\n")
    src = '''import logs
let r = logs.parse_line("Mar 12 09:14:05 web01 sshd[1]: Invalid user bob from 203.0.113.42 port 22")
say(r.format, r.action, r.user, r.ip)
say(len(logs.parse("a\\nb\\n\\nc", "text")), logs.parse("x", "text")[0].message)
say(len(logs.read("big.log", "auth", {"limit": 10})))
let seen = []
say(logs.each("big.log", fn(rec) { seen.push(rec.ip) }))
say(len(seen), seen[0])
say(logs.top(logs.read("big.log"), "ip", 2))
say(logs.formats)'''
    out = run_tb(src)
    assert out[:5] == ["auth invalid_user bob 203.0.113.42", "3 x", "10", "300", "300 192.0.2.0"]
    assert out[5] == '[{"value": "192.0.2.0", "count": 100}, {"value": "192.0.2.1", "count": 100}]'
    assert out[6] == '["auto", "syslog", "auth", "apache", "nginx", "json", "csv", "windows", "text"]'


def test_each_stops_when_fn_gives_false(tmp_path):
    (tmp_path / "a.log").write_text("one\ntwo\nthree\n")
    src = 'import logs\nlet n = 0\nsay(logs.each("a.log", fn(r) { n += 1\n return n < 2 }))\nsay(n)'
    assert run_tb(src) == ["2", "2"]


def test_top_counts_repeats():
    src = '''import logs
let rs = logs.parse("Mar 12 09:14:09 h sshd[9]: message repeated 4 times: [ Failed password for root from 203.0.113.42 port 1 ssh2]\\nMar 12 09:14:10 h sshd[9]: Failed password for root from 192.0.2.1 port 1 ssh2", "auth")
say(logs.top(rs, "ip"))'''
    assert run_tb(src) == ['[{"value": "203.0.113.42", "count": 4}, {"value": "192.0.2.1", "count": 1}]']


@pytest.mark.parametrize("src,message", [
    ('logs.read("missing.log")', "I couldn't find 'missing.log'"),
    ('logs.read(".")', "is a folder, not a file."),
    ('logs.read("")', "logs.read() needs a path, but got empty text."),
    ('logs.parse_line("x", "xml")', 'logs.parse_line() doesn\'t know the format "xml". Formats: auto, syslog'),
    ('logs.parse("x", "auto", {"limit": "ten"})', "logs.parse() needs the limit to be a whole number"),
    ("logs.parse_line(5)", "logs.parse_line() needs line to be text, not a number."),
    ('logs.parse("[1,", "json")', "That log isn't valid JSON"),
    ('logs.top("x", "ip")', "logs.top() needs records to be a list"),
    ('logs.each("missing.log", fn(r) { })', "I couldn't find 'missing.log'"),
])
def test_logs_errors(src, message):
    assert message in str(tb_error(f"import logs\n{src}"))
