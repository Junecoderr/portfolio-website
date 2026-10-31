"""logs: Zeek, Suricata, CloudTrail, Okta, Microsoft 365, CEF, LEEF and Sysmon."""

import datetime as dt
import json

import pytest

from conftest import run_tb
from tblib.logs import LineParser, iter_file, iter_text, sniff_text


def utc(*parts):
    return dt.datetime(*parts, tzinfo=dt.timezone.utc).timestamp()


def records(text, fmt="auto"):
    return list(iter_text(text, fmt, 1))


def parse(line, fmt="auto"):
    return LineParser(fmt, 2026).parse(line)


# -- the sample files ---------------------------------------------------------

@pytest.mark.parametrize("name,fmt,count", [
    ("zeek_conn.log", "zeek", 8), ("zeek_dns.json", "zeek", 6), ("suricata_eve.json", "suricata", 7),
    ("cloudtrail.json", "cloudtrail", 7), ("okta_system_log.json", "okta", 5), ("m365_audit.json", "m365", 5),
    ("m365_audit.csv", "m365", 5), ("firewall_cef.log", "cef", 5), ("qradar_leef.log", "leef", 4),
    ("sysmon.json", "windows", 5),
])
def test_sample_files_sniff_and_read(root, name, fmt, count):
    path = str(root / "apps" / "data" / name)
    out = run_tb(f'import logs\nlet rs = logs.read("{path}")\nsay(logs.sniff("{path}"), len(rs), rs[0].format)')
    assert out == [f"{fmt} {count} {fmt}"]


def test_samples_use_documentation_addresses_only(root):
    import ipaddress
    import re
    docs = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
    for name in ("zeek_conn.log", "zeek_dns.json", "suricata_eve.json", "cloudtrail.json", "okta_system_log.json",
                 "m365_audit.json", "m365_audit.csv", "firewall_cef.log", "qradar_leef.log", "sysmon.json"):
        text = (root / "apps" / "data" / name).read_text(encoding="utf-8")
        for found in re.findall(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])", text):
            ip = ipaddress.ip_address(found)
            assert ip.is_unspecified or any(ip in n for n in docs), (name, found)


# -- Zeek -----------------------------------------------------------------------

ZEEK = ("#separator \\x09\r\n#set_separator\t,\r\n#empty_field\t(empty)\r\n#unset_field\t-\r\n#path\tssh\r\n"
        "#fields\tts\tuid\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tauth_success\tauth_attempts\tclient\tcipher_alg\ttunnel_parents\r\n"
        "#types\ttime\tstring\taddr\tport\taddr\tport\tbool\tcount\tstring\tstring\tset[string]\r\n"
        "1773302441.552910\tC3n8\t203.0.113.66\t51544\t192.0.2.20\t22\tF\t6\tSSH-2.0-libssh\\x09x\t-\t(empty)\r\n"
        "1773302500.000000\tC4n9\t198.51.100.4\t40000\t192.0.2.20\t22\tT\t1\tSSH-2.0-OpenSSH_9.6\taes128-ctr\tA,B\r\n"
        "#close\t2026-03-12-09-00-00\r\n")


def test_zeek_tsv_types_headers_and_crlf():
    assert sniff_text(ZEEK) == "zeek"
    a, b = records(ZEEK)
    assert a["format"] == "zeek" and a["time"] == pytest.approx(1773302441.55291)
    assert (a["log"], a["action"], a["ip"], a["src_port"], a["dst_ip"], a["dst_port"], a["port"]) == (
        "ssh", "ssh", "203.0.113.66", 51544, "192.0.2.20", 22, 22)
    assert (a["success"], a["auth_attempts"], a["cipher_alg"], a["tunnel_parents"]) == (False, 6, None, [])
    assert a["client"] == "SSH-2.0-libssh\tx"  # \x09 inside a value
    assert a["id.orig_h"] == "203.0.113.66" and a["uid"] == "C3n8"
    assert (b["success"], b["tunnel_parents"]) == (True, ["A", "B"])
    assert a["raw"].startswith("1773302441.552910\tC3n8")


def test_zeek_json_lines_and_nested_id():
    line = json.dumps({"ts": "2026-03-12T08:01:13.017342Z", "uid": "Ck", "id.orig_h": "192.0.2.10", "id.orig_p": 53122,
                       "id.resp_h": "192.0.2.53", "id.resp_p": 53, "proto": "udp", "query": "www.example.com",
                       "_path": "dns"})
    r = parse(line)
    assert (r["format"], r["log"], r["query"], r["proto"], r["when"], r["zone"]) == (
        "zeek", "dns", "www.example.com", "udp", "2026-03-12 08:01:13", "UTC")
    nested = parse(json.dumps({"ts": 1773302413.0, "uid": "Cx", "id": {"orig_h": "192.0.2.9", "resp_h": "192.0.2.1"}}))
    assert (nested["format"], nested["ip"], nested["dst_ip"]) == ("zeek", "192.0.2.9", "192.0.2.1")


# -- Suricata -------------------------------------------------------------------

def test_suricata_alert_and_dns_v2():
    alert = parse(json.dumps({"timestamp": "2026-03-12T08:03:00.004519+0000", "flow_id": 9, "event_type": "alert",
                              "src_ip": "192.0.2.31", "src_port": 50890, "dest_ip": "203.0.113.200", "dest_port": 4444,
                              "proto": "TCP", "alert": {"action": "blocked", "signature_id": 2025644,
                                                        "signature": "ET MALWARE Possible Reverse Shell", "category": "Trojan"}}))
    assert alert["format"] == "suricata" and alert["time"] == pytest.approx(utc(2026, 3, 12, 8, 3, 0, 4519))
    assert (alert["action"], alert["rule"], alert["rule_id"], alert["category"], alert["message"]) == (
        "alert", "ET MALWARE Possible Reverse Shell", 2025644, "Trojan", "ET MALWARE Possible Reverse Shell")
    assert (alert["ip"], alert["dst_ip"], alert["dst_port"], alert["dest_ip"], alert["alert"]["action"]) == (
        "192.0.2.31", "203.0.113.200", 4444, "203.0.113.200", "blocked")
    dns = parse(json.dumps({"timestamp": "2026-03-12T08:01:13+0000", "event_type": "dns", "src_ip": "192.0.2.10",
                            "dest_ip": "192.0.2.53", "dns": {"type": "query", "rrname": "example.com"}}))
    assert (dns["query"], dns["message"]) == ("example.com", "dns example.com")


# -- CloudTrail -----------------------------------------------------------------

def trail(name, **extra):
    event = {"eventVersion": "1.09", "userIdentity": {"type": "IAMUser", "userName": "alice",
                                                      "arn": "arn:aws:iam::123456789012:user/alice"},
             "eventTime": "2026-03-12T08:14:05Z", "eventSource": "iam.amazonaws.com", "eventName": name,
             "awsRegion": "us-east-1", "sourceIPAddress": "203.0.113.66", "eventID": "e1"}
    event.update(extra)
    return event


def test_cloudtrail_records_file_one_line_and_json_lines(tmp_path):
    events = [trail("CreateUser"), trail("StopLogging", errorCode="AccessDenied", sourceIPAddress="AWS Internal",
                                         userIdentity={"type": "Root", "arn": "arn:aws:iam::123456789012:root"})]
    for name, text in [("a.json", json.dumps({"Records": events}, indent=2)), ("b.json", json.dumps({"Records": events})),
                       ("c.json", "\r\n".join(json.dumps(e) for e in events) + "\r\n")]:
        (tmp_path / name).write_bytes(text.encode())
        a, b = list(iter_file(str(tmp_path / name), "auto", 1))
        assert (a["format"], a["user"], a["ip"], a["action"], a["success"], a["service"], a["region"]) == (
            "cloudtrail", "alice", "203.0.113.66", "CreateUser", True, "iam.amazonaws.com", "us-east-1"), name
        assert a["time"] == utc(2026, 3, 12, 8, 14, 5) and a["eventID"] == "e1"
        assert (b["user"], b["ip"], b["success"], b["error"]) == ("root", None, False, "AccessDenied")
        assert b["sourceIPAddress"] == "AWS Internal"


def test_cloudtrail_console_login_failure_and_assumed_role():
    r = parse(json.dumps(trail("ConsoleLogin", responseElements={"ConsoleLogin": "Failure"})))
    assert (r["action"], r["success"]) == ("ConsoleLogin", False)
    role = parse(json.dumps(trail("GetObject", userIdentity={
        "type": "AssumedRole", "arn": "arn:aws:sts::123456789012:assumed-role/Admin/bob",
        "sessionContext": {"sessionIssuer": {"type": "Role", "userName": "Admin"}}})))
    assert role["user"] == "Admin"


# -- Okta -----------------------------------------------------------------------

def test_okta_event():
    r = parse(json.dumps({"uuid": "u1", "published": "2026-03-12T08:09:58.204Z", "eventType": "user.session.start",
                          "displayMessage": "User login to Okta", "severity": "WARN",
                          "actor": {"type": "User", "alternateId": "alice@example.com"},
                          "client": {"ipAddress": "203.0.113.66", "userAgent": {"rawUserAgent": "curl/8.5.0"}},
                          "outcome": {"result": "FAILURE", "reason": "INVALID_CREDENTIALS"},
                          "target": [{"type": "AppInstance", "alternateId": "app"}, {"type": "User", "alternateId": "bob@example.com"}]}))
    assert (r["format"], r["user"], r["ip"], r["action"], r["success"], r["reason"], r["message"]) == (
        "okta", "alice@example.com", "203.0.113.66", "user.session.start", False, "INVALID_CREDENTIALS", "User login to Okta")
    assert (r["agent"], r["target_user"], r["severity"], r["time"]) == (
        "curl/8.5.0", "bob@example.com", "WARN", pytest.approx(utc(2026, 3, 12, 8, 9, 58, 204000)))


# -- Microsoft 365 ---------------------------------------------------------------

def test_m365_times_are_utc_and_client_ip_loses_its_port():
    r = parse(json.dumps({"CreationTime": "2026-03-12T08:13:02", "Id": "x", "Operation": "New-InboxRule",
                          "Workload": "Exchange", "ResultStatus": "True", "UserId": "carol@example.com",
                          "ClientIP": "203.0.113.66:50912"}))
    assert (r["format"], r["time"], r["zone"], r["user"], r["ip"], r["action"], r["success"], r["service"]) == (
        "m365", utc(2026, 3, 12, 8, 13, 2), "UTC", "carol@example.com", "203.0.113.66", "New-InboxRule", True, "Exchange")
    assert r["ClientIP"] == "203.0.113.66:50912"
    v6 = parse(json.dumps({"CreationTime": "2026-03-12T08:13:02", "Operation": "MailItemsAccessed", "Workload": "Exchange",
                           "ClientIPAddress": "[2001:db8::5]:443"}))
    assert v6["ip"] == "2001:db8::5"


def test_m365_search_unifiedauditlog_objects_and_csv(tmp_path):
    audit = {"CreationTime": "2026-03-12T08:09:51", "Operation": "UserLoginFailed", "Workload": "AzureActiveDirectory",
             "ResultStatus": "Failed", "UserId": "carol@example.com", "ClientIP": "203.0.113.66",
             "LogonError": "InvalidUserNameOrPassword"}
    ps = {"RecordType": "AzureActiveDirectoryStsLogon", "CreationDate": "/Date(1773302991000)/",
          "UserIds": "carol@example.com", "Operations": "UserLoginFailed", "AuditData": json.dumps(audit)}
    (r,) = records(json.dumps([ps]))
    assert (r["format"], r["user"], r["success"], r["reason"], r["UserIds"]) == (
        "m365", "carol@example.com", False, "InvalidUserNameOrPassword", "carol@example.com")
    assert "AuditData" not in r and r["time"] == utc(2026, 3, 12, 8, 9, 51)
    csv_text = ('CreationDate,UserIds,Operations,AuditData\r\n'
                '2026-03-12T08:09:51.0000000Z,carol@example.com,UserLoginFailed,"' + json.dumps(audit).replace('"', '""') + '"\r\n')
    (tmp_path / "audit.csv").write_bytes(csv_text.encode())
    (c,) = list(iter_file(str(tmp_path / "audit.csv"), "auto", 1))
    assert (c["format"], c["action"], c["ip"], c["service"], c["CreationDate"]) == (
        "m365", "UserLoginFailed", "203.0.113.66", "AzureActiveDirectory", "2026-03-12T08:09:51.0000000Z")


# -- CEF and LEEF ---------------------------------------------------------------

def test_cef_syslog_wrapped_with_escapes_and_labels():
    line = ("<14>Mar 12 08:00:42 fw01 CEF:0|Example\\|Corp|NGFW|1.0|100|Blocked|5|rt=Mar 12 2026 08:00:42 UTC "
            "src=203.0.113.66 spt=51547 dst=192.0.2.20 dpt=3389 proto=TCP act=deny suser=CORP\\\\amy "
            "msg=blocked a\\=b c cs1Label=Rule cs1=deny all outcome=failure")
    r = parse(line)
    assert r["format"] == "cef" and r["time"] == utc(2026, 3, 12, 8, 0, 42)
    assert (r["vendor"], r["product"], r["event_id"], r["name"], r["severity"], r["cef_version"]) == (
        "Example|Corp", "NGFW", "100", "Blocked", 5, 0)
    assert (r["host"], r["ip"], r["src_port"], r["dst_ip"], r["dst_port"], r["port"], r["proto"]) == (
        "fw01", "203.0.113.66", 51547, "192.0.2.20", 3389, 3389, "TCP")
    assert (r["action"], r["user"], r["success"], r["message"], r["Rule"], r["cs1"]) == (
        "deny", "CORP\\amy", False, "blocked a=b c", "deny all", "deny all")
    assert (r["syslog_facility"], r["syslog_severity"]) == (1, "info")


def test_cef_bare_line_epoch_time_and_crlf_file(tmp_path):
    line = "CEF:0|Trend Micro|Deep Security Agent|20.0|4000000|Eicar_test_file|6|dvchost=web02.example.com act=Delete rt=1773303000000"
    r = parse(line)
    assert (r["format"], r["host"], r["action"], r["time"], r["message"]) == (
        "cef", "web02.example.com", "Delete", 1773303000, "Eicar_test_file")
    (tmp_path / "cef.log").write_bytes((line + "\r\n" + line + "\r\n").encode())
    rs = list(iter_file(str(tmp_path / "cef.log"), "auto", 1))
    assert len(rs) == 2 and rs[1]["rt"] == "1773303000000" and not rs[1]["raw"].endswith("\r")


def test_leef_1_and_2():
    one = parse("<13>Mar 12 08:09:40 fw02 LEEF:1.0|Fortinet|FortiGate|7.2|32002|"
                "devTime=2026-03-12T08:09:40Z\tsrc=203.0.113.66\tdst=192.0.2.1\tdstPort=443\tproto=TCP\tusrName=admin")
    assert (one["format"], one["leef_version"], one["vendor"], one["event_id"], one["action"], one["host"]) == (
        "leef", "1.0", "Fortinet", "32002", "32002", "fw02")
    assert (one["ip"], one["dst_ip"], one["dst_port"], one["user"], one["time"]) == (
        "203.0.113.66", "192.0.2.1", 443, "admin", utc(2026, 3, 12, 8, 9, 40))
    caret = parse("LEEF:2.0|Bitdefender|GravityZone|7.9|av|^|devTime=1773303112000^src=192.0.2.31^usrName=dave")
    assert (caret["ip"], caret["user"], caret["time"]) == ("192.0.2.31", "dave", 1773303112)
    hexed = parse("LEEF:2.0|Lancope|StealthWatch|7.4|41|x09|src=192.0.2.31\tdstPort=4444")
    assert (hexed["ip"], hexed["dst_port"]) == ("192.0.2.31", 4444)


# -- Sysmon ---------------------------------------------------------------------

def test_sysmon_eventdata_and_message_forms():
    evtx_style = {"ProviderName": "Microsoft-Windows-Sysmon", "EventID": 3, "Computer": "WS01",
                  "Channel": "Microsoft-Windows-Sysmon/Operational", "TimeCreated": "2026-03-12T08:03:00Z",
                  "EventData": {"Image": "C:\\Windows\\System32\\cmd.exe", "User": "CORP\\dave", "Protocol": "tcp",
                                "SourceIp": "192.0.2.31", "SourcePort": "50890", "DestinationIp": "203.0.113.200",
                                "DestinationPort": "4444", "DestinationHostname": "-"}}
    r = parse(json.dumps(evtx_style), "windows")
    assert (r["format"], r["action"], r["message"], r["user"], r["domain"], r["computer"]) == (
        "windows", "network_connection", "Network connection", "dave", "CORP", "WS01")
    assert (r["ip"], r["src_port"], r["dst_ip"], r["dst_port"], r["port"], r["proto"], r["process"]) == (
        "192.0.2.31", 50890, "203.0.113.200", 4444, 4444, "tcp", "C:\\Windows\\System32\\cmd.exe")
    assert r["DestinationIp"] == "203.0.113.200" and "dst_host" not in r
    message = ("Process Create:\r\nRuleName: -\r\nUtcTime: 2026-03-12 08:02:10.441\r\nProcessId: 6812\r\n"
               "Image: C:\\Windows\\System32\\whoami.exe\r\nCommandLine: whoami /all\r\nUser: CORP\\dave\r\n"
               "ParentImage: C:\\Windows\\System32\\cmd.exe\r\nHashes: SHA256=AB")
    p = parse(json.dumps({"Id": 1, "ProviderName": "Microsoft-Windows-Sysmon", "Message": message}))
    assert (p["format"], p["action"], p["user"], p["pid"], p["command_line"], p["parent_process"], p["hashes"]) == (
        "windows", "process_created", "dave", 6812, "whoami /all", "C:\\Windows\\System32\\cmd.exe", "SHA256=AB")
    assert (p["CommandLine"], p["Image"], p["time"]) == ("whoami /all", "C:\\Windows\\System32\\whoami.exe",
                                                         pytest.approx(utc(2026, 3, 12, 8, 2, 10, 441000)))
    # The same ids from the Security log keep their Security meaning.
    assert parse(json.dumps({"EventID": 1102, "ProviderName": "Microsoft-Windows-Eventlog"}))["action"] == "audit_log_cleared"


# -- detection stays put for the older formats ------------------------------------

def test_lookalike_json_stays_generic():
    assert sniff_text('{"timestamp": "2026-03-12T08:00:00Z", "event_type": "login", "user": "a"}\n') == "json"
    assert sniff_text('{"eventName": "x", "time": 1}\n') == "json"
    assert sniff_text('{"ts": 1, "msg": "x"}\n') == "json"
    assert sniff_text('[{"Id": 4624, "Message": "x"}]') == "windows"
    assert parse('{"msg": "CEF:0|a|b|c|d|e|f|g"}')["format"] == "json"
    assert sniff_text("Mar 10 10:00:00 h sshd[1]: CEF is not here\n") == "auth"
    assert sniff_text("time,event_type\n1,2\n") == "csv"


def test_tb_reads_a_new_format(root):
    path = str(root / "apps" / "data" / "cloudtrail.json")
    src = f'''import logs
let rs = logs.read("{path}")
let failed = rs.filter(fn(r) {{ return r.success == false }})
say(len(failed), failed[0].action, failed[0].user, failed[0].ip)
say(logs.top(rs, "user", 1))'''
    assert run_tb(src) == ["2 ConsoleLogin alice 203.0.113.66", '[{"value": "alice", "count": 4}]']
