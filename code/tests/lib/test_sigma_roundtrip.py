"""Sigma -> SIEM query round trip on a test subset of public-style Sigma rules
(tests/lib/sigma_subset/, written for TB): every rule through every backend, checked for
structure (balanced brackets, field names that went through the field map, real table
columns for KQL) and for the operators its modifiers need, plus exact output for a few."""

import re
from pathlib import Path

import pytest

from conftest import run_tb, tb_error
from tblib.detect import BACKEND_CLASSES, BACKENDS, ESQL_NAME, RuleError, sigma_rules, sigma_to_query

SUBSET = Path(__file__).parent / "sigma_subset"
RULES = [(path.name, rule) for path in sorted(SUBSET.glob("*.yml"))
         for rule in sigma_rules(path.read_text(encoding="utf-8"), path.name)]
BY_NAME = {rule["name"]: rule for _f, rule in RULES}

# Things a backend can't say: (rule, backend) -> words the friendly refusal must contain.
REFUSALS = {("Failures then success in order", b): "temporal_ordered" for b in BACKENDS}

# Columns of the Sentinel tables the KQL backend writes (Microsoft's schemas, the parts we use).
KQL_COLUMNS = {
    "Syslog": {"TimeGenerated", "Computer", "HostName", "HostIP", "Facility", "SeverityLevel", "ProcessName",
               "ProcessID", "SyslogMessage", "EventTime"},
    "CommonSecurityLog": {
        "TimeGenerated", "SourceIP", "DestinationIP", "SourcePort", "DestinationPort", "DeviceAction", "SourceUserName",
        "DestinationUserName", "DeviceName", "Message", "Protocol", "DeviceVendor", "DeviceProduct", "Activity",
        "DeviceEventClassID", "LogSeverity", "EventOutcome", "SourceHostName", "DestinationHostName", "DeviceAddress",
        "ApplicationProtocol", "Reason", "DeviceEventCategory", "RequestURL", "RequestMethod", "RequestClientApplication",
        "RequestContext", "FileName", "FileHash", "ReceivedBytes", "SentBytes", "EventCount", "SourceMACAddress",
        "DestinationMACAddress", "SourceProcessName", "DestinationProcessName", "DeviceExternalID",
        *{f"DeviceCustomString{i}" for i in range(1, 7)}},
    "SecurityEvent": {"TimeGenerated", "EventID", "Computer", "Account", "TargetUserName", "SubjectUserName",
                      "IpAddress", "LogonType", "NewProcessName", "ParentProcessName", "CommandLine"},
    "DeviceProcessEvents": {"TimeGenerated", "FolderPath", "ProcessCommandLine", "InitiatingProcessFolderPath",
                            "InitiatingProcessCommandLine", "AccountName", "DeviceName", "FileName",
                            "ProcessVersionInfoOriginalFileName", "ProcessIntegrityLevel", "MD5", "SHA256"},
    "W3CIISLog": {"TimeGenerated", "csUriStem", "csUserAgent", "scStatus", "csMethod", "cIP", "csUserName"},
    "DnsEvents": {"TimeGenerated", "Name", "QueryType", "ClientIP", "IPAddresses"},
    "AWSCloudTrail": {"TimeGenerated", "EventName", "EventSource", "SourceIpAddress", "ErrorCode", "ErrorMessage",
                      "UserIdentityType", "UserIdentityArn", "UserIdentityAccountId", "AWSRegion", "UserAgent",
                      "UserIdentityUserName", "AdditionalEventData"},
    "OfficeActivity": {"TimeGenerated", "UserId", "ClientIP", "Operation", "Parameters", "Workload", "ResultStatus"},
    "Okta_CL": {"TimeGenerated", "eventType_s", "displayMessage_s", "actor_alternateId_s", "client_ipAddress_s"},
    "_Im_NetworkSession": {"TimeGenerated", "SrcIpAddr", "SrcPortNumber", "DstIpAddr", "DstPortNumber", "NetworkProtocol"},
    "_Im_Dns": {"TimeGenerated", "SrcIpAddr", "DstIpAddr", "DnsQuery", "DnsQueryTypeName", "DnsResponseCodeName"},
    "_Im_WebSession": {"TimeGenerated", "SrcIpAddr", "DstIpAddr", "Url", "HttpUserAgent", "HttpRequestMethod",
                       "HttpStatusCode"},
}
KQL_WORDS = {"where", "extend", "summarize", "by", "in", "and", "or", "not", "case", "extract", "toint", "matches",
             "regex", "contains", "contains_cs", "startswith", "startswith_cs", "endswith", "endswith_cs", "count",
             "dcount", "bin", "ipv4_is_in_range", "ipv6_is_in_range", "coalesce", "isempty", "isnotempty", "union",
             "make_set", "set_has_element", "true", "false"}

# Modifier / condition feature -> what each backend must write for it.
FEATURES = [
    (r"\|contains", {"splunk": '="*', "elastic": ":*", "esql": " LIKE ", "kql": " contains "}),
    (r"\|startswith", {"splunk": '*"', "elastic": "*", "esql": " LIKE ", "kql": " startswith "}),
    (r"\|endswith", {"splunk": '="*', "elastic": ":*", "esql": " LIKE ", "kql": " endswith "}),
    (r"\|re\b", {"splunk": "| regex ", "elastic": ":/", "esql": " RLIKE ", "kql": " matches regex "}),
    (r"\|cidr", {"splunk": '="1', "elastic": '/', "esql": "CIDR_MATCH(", "kql": "ipv4_is_in_range("}),
    (r"\|exists", {"splunk": "=*", "elastic": "_exists_:", "esql": " IS ", "kql": "isempty("}),
    (r"\| ?count\(", {"splunk": "| stats ", "elastic": "| STATS ", "esql": "| STATS ", "kql": "| summarize "}),
    (r"\| ?near ", {"splunk": "values(sigma_rule)", "elastic": "MAX(", "esql": "MAX(", "kql": "make_set(sigma_rule)"}),
    (r"\bnot\b", {"splunk": "NOT ", "elastic": "NOT ", "esql": "NOT ", "kql": "not("}),
]
CORRELATION_OPS = {"splunk": "| stats ", "elastic": "| STATS ", "esql": "| STATS ", "kql": "| summarize "}
MULTI_RULE_OPS = {"splunk": "| multisearch [search ", "elastic": " OR ", "esql": " OR ", "kql": "union\n("}


def strip_strings(query, backend):
    """The query with string literals (and Lucene /regex/) emptied, so brackets and names inside don't count."""
    if backend == "esql":
        query = re.sub(r'"""[\s\S]*?"""', '""', query)
    query = re.sub(r'@?"(?:[^"\\]|\\.)*"', '""', query)
    if backend == "elastic":
        query = re.sub(r":/(?:[^/\\]|\\.)*/", ':""', query)
        query = re.sub(r"\\.", "_", query)  # Lucene escapes like \( are literal characters
    return query


def balanced(text):
    stack, pairs = [], {")": "(", "]": "["}
    for ch in text:
        if ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


def field_names(query, backend):
    """Field names a query compares, by backend."""
    text = strip_strings(query, backend)
    if backend == "splunk":
        return set(re.findall(r"(?<![\w.\-])([A-Za-z_][\w.\-]*)\s*(?:!=|>=|<=|=|>|<)", text)) - {"index"}
    if backend == "elastic":
        return {m for m in re.findall(r"(?:^|[\s(])(?:_exists_:)?([\w.@\-]+):", text) if m != "_exists_"}
    if backend == "esql":
        names = re.findall(r"(?:TO_LOWER|TO_STRING|CIDR_MATCH|MAX|COUNT_DISTINCT)\((`[^`]+`|[\w.@]+)", text)
        names += re.findall(r"(`[^`]+`|[\w.@]+) (?:RLIKE|IS NULL|IS NOT NULL|==|LIKE)", text)
        return {n for n in names if not n.startswith(("TO_", "CASE"))}
    words = set()
    made = set(re.findall(r"\| (?:extend|summarize) (\w+) =", text)) | {"sigma_rule", "rules", "count_"}
    for token in re.findall(r"(?<![\w.])[A-Za-z_]\w*(?:\.\w+)*", text):
        first = token.split(".")[0]
        if first not in KQL_WORDS and first not in made and first not in KQL_COLUMNS and first != "Event":
            words.add(first)
    return words


def raw_mapped_names(rule, backend):
    """Sigma field names the backend renames for this rule; they must not appear as written."""
    fields = BACKEND_CLASSES["esql" if backend == "elastic" else backend](rule, None).fields
    return {k for k, v in fields.items() if k != v} - set(fields.values())


def test_subset_is_big_enough():
    assert len(RULES) >= 40
    kinds = {r["correlation"]["type"] for _f, r in RULES if r.get("correlation")}
    assert kinds == {"event_count", "value_count", "temporal", "temporal_ordered"}
    assert any(r.get("near") for _f, r in RULES) and any(r.get("aggregation") for _f, r in RULES)
    products = {(r.get("logsource") or {}).get("product") for _f, r in RULES}
    assert {"windows", "linux", "aws", "okta", "m365", "zeek"} <= products


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", [r["name"] for _f, r in RULES])
def test_round_trip(name, backend):
    rule = BY_NAME[name]
    if (name, backend) in REFUSALS:
        with pytest.raises(RuleError) as info:
            sigma_to_query(rule, backend)
        assert REFUSALS[name, backend] in str(info.value)
        return
    query = sigma_to_query(rule, backend)
    assert query.strip() and query == sigma_to_query(rule, backend)  # deterministic
    bare = strip_strings(query, backend)
    assert balanced(bare), query
    assert bare.count('"') % 2 == 0, query
    names = field_names(query, backend)
    for n in names:
        assert re.match(r"^[A-Za-z_@][\w.@\-]*$", n) or (backend == "esql" and n.startswith("`")), (n, query)
    if not rule.get("correlation"):
        leaked = names & raw_mapped_names(rule, backend)
        assert not leaked, (leaked, query)
    if backend == "kql":
        table = query.split("\n")[0] if not query.startswith("union") else None
        tables = [table] if table else re.findall(r"^\((\w+)$", query, re.MULTILINE)
        for t in tables:
            if t in KQL_COLUMNS:
                allowed = set().union(*(KQL_COLUMNS[x] for x in tables if x in KQL_COLUMNS))
                assert names <= allowed, (names - allowed, query)
    if backend in ("esql", "elastic") and query.startswith("FROM "):
        for n in names:
            assert ESQL_NAME.match(n) or n.startswith("`"), n
    source = repr(rule.get("detection")) + " " + repr(rule.get("condition"))
    for pattern, want in FEATURES:
        if re.search(pattern, source) and not (pattern == r"\bnot\b" and rule.get("near")):
            if backend == "elastic" and query.startswith("FROM "):
                want = dict(want, elastic=want["esql"])
            assert want[backend] in query, (pattern, backend, query)
    if rule.get("correlation"):
        assert CORRELATION_OPS[backend] in query
        if len(rule["correlation"]["rules"]) > 1:
            assert MULTI_RULE_OPS[backend] in query


# -- exact output for a handful --------------------------------------------------------------

EXACT = {
    ("Security log cleared", "splunk"): 'source="WinEventLog:Security" EventCode=1102',
    ("Security log cleared", "elastic"): "(winlog.channel:Security) AND event.code:1102",
    ("Security log cleared", "esql"): 'FROM logs-*\n| WHERE winlog.channel == "Security" AND TO_STRING(event.code) == "1102"',
    ("Security log cleared", "kql"): "SecurityEvent\n| where EventID == 1102",
    ("Encoded PowerShell command line", "kql"):
        'DeviceProcessEvents\n| where (FolderPath endswith "\\\\powershell.exe" or FolderPath endswith "\\\\pwsh.exe") and '
        '(ProcessCommandLine contains " -enc " or ProcessCommandLine contains " -EncodedCommand ")',
    ("Firewall denies from one source to many ports", "kql"):
        'CommonSecurityLog\n| where DeviceAction =~ "deny"\n'
        "| summarize distinct_count = dcount(DestinationPort) by SourceIP, bin(TimeGenerated, 60s)\n"
        "| where distinct_count > 20",
    ("Outbound SMB to the internet", "splunk"):
        'sourcetype="firewall" dst_port=445 action="allow" (NOT (dst_ip="10.*" OR dst_ip="172.16.0.0/12" OR '
        'dst_ip="192.168.*"))',
    ("Many SSH failures from one address", "splunk"):
        'sourcetype="linux_secure" action="failed_login" | bin _time span=300s | stats count as event_count by _time, src '
        "| where event_count >= 20",
    ("Many SSH failures from one address", "esql"):
        'FROM logs-*\n| WHERE (event.dataset == "system.auth" AND process.name == "sshd") AND '
        'TO_LOWER(event.action) == "failed_login"\n'
        "| STATS event_count = COUNT(*) BY source.ip, bucket = BUCKET(@timestamp, 5 minutes)\n| WHERE event_count >= 20",
    ("Failures on Linux and Windows from one address", "splunk"):
        '| multisearch [search sourcetype="linux_secure" action="failed_login" | eval source=src | eval sigma_rule="SSH '
        'failed login"] [search source="WinEventLog:Security" EventCode=4625 | eval source=IpAddress | eval '
        'sigma_rule="Windows failed logon"] | bin _time span=900s | stats dc(sigma_rule) as rule_count by _time, source '
        "| where rule_count >= 2",
    ("SSH success soon after a failure", "esql"):
        'FROM logs-*\n| WHERE ((event.dataset == "system.auth" AND process.name == "sshd") AND TO_LOWER(event.action) == '
        '"login") OR ((event.dataset == "system.auth" AND process.name == "sshd") AND TO_LOWER(event.action) == '
        '"failed_login") OR ((event.dataset == "system.auth" AND process.name == "sshd") AND TO_LOWER(user.name) == '
        '"nagios")\n'
        '| EVAL near_anchor = CASE(((event.dataset == "system.auth" AND process.name == "sshd") AND TO_LOWER(event.action) '
        '== "login"), 1, 0), bad = CASE(((event.dataset == "system.auth" AND process.name == "sshd") AND '
        'TO_LOWER(event.action) == "failed_login"), 1, 0), monitoring = CASE(((event.dataset == "system.auth" AND '
        'process.name == "sshd") AND TO_LOWER(user.name) == "nagios"), 1, 0)\n'
        "| STATS near_anchor = MAX(near_anchor), bad = MAX(bad), monitoring = MAX(monitoring) BY bucket = "
        "BUCKET(@timestamp, 1 minute)\n| WHERE near_anchor > 0 AND bad > 0 AND monitoring == 0",
}


@pytest.mark.parametrize("name,backend", list(EXACT))
def test_exact_output(name, backend):
    assert sigma_to_query(BY_NAME[name], backend) == EXACT[name, backend]


def test_kql_temporal_union_renames_aliases():
    q = sigma_to_query(BY_NAME["Failures on Linux and Windows from one address"], "kql")
    assert q.startswith("union\n(Syslog\n")
    assert "| extend source = SrcIp\n| extend sigma_rule = \"SSH failed login\")," in q
    assert "(SecurityEvent\n| where EventID == 4625\n| extend source = IpAddress\n" in q
    assert q.endswith("| summarize rule_count = dcount(sigma_rule) by source, bin(TimeGenerated, 900s)\n"
                      "| where rule_count >= 2")


def test_through_tb_and_friendly_refusals():
    import json
    folder = SUBSET.as_posix()
    out = run_tb(f'import detect\nlet rules = detect.sigma_folder("{folder}")\nlet n = 0\n'
                 'for r in rules.filter(fn(x) { return x.name != "Failures then success in order" }) {\n'
                 '  for b in detect.backends {\n    if len(detect.sigma_to_query(r, b)) > 0 { n += 1 }\n  }\n}\nsay(n)')
    assert out == [str((len(RULES) - 1) * len(BACKENDS))]
    err = str(tb_error(f'import detect\nlet r = detect.sigma_folder("{folder}").filter(fn(x) {{ return x.name == '
                       '"Failures then success in order" })[0]\ndetect.sigma_to_query(r, "kql")'))
    assert "temporal_ordered" in err and "use type: temporal" in err
    text = "title: t\ndetection:\n  sel:\n    cmd|re|i: 'evil'\n  condition: sel\n"
    err = str(tb_error(f"import detect\ndetect.sigma_to_query(detect.sigma({json.dumps(text)}), \"esql\")"))
    assert "case-insensitive |re" in err
