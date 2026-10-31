import pytest

import tb
from conftest import run_tb, tb_error
from tblib.detect import YamlError, read_yaml


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(source):
    return run_tb("import detect\nimport logs\n" + source)


# -- rule() and where matching ----------------------------------------------------

def test_rule_fills_defaults():
    out = run('let r = detect.rule({"name": "x", "where": {"a": 1}})\nsay(r.level, r.count, r.within, r.by)')
    assert out == ["medium 1 nil nil"]
    assert run('say(detect.rule({"name": "x", "where": {"a": 1}, "within": "5m", "level": "HIGH"}).within)') == ["300"]


@pytest.mark.parametrize("where,record,expected", [
    ('{"user": "root"}', '{"user": "ROOT"}', True),                       # exact, any case
    ('{"user": "root"}', '{"user": "rooted"}', False),
    ('{"user": "ro*"}', '{"user": "rooted"}', True),                      # wildcard
    ('{"user": "r?ot"}', '{"user": "riot"}', True),
    ('{"user": "r\\\\*t"}', '{"user": "r*t"}', True),                       # escaped wildcard
    ('{"user": "r\\\\*t"}', '{"user": "rat"}', False),
    ('{"cmd": "re:^/bin/(ba)?sh$"}', '{"cmd": "/bin/bash"}', True),       # regex
    ('{"cmd": "re:^/bin/(ba)?sh$"}', '{"cmd": "/bin/zsh"}', False),
    ('{"status": 404}', '{"status": 404}', True),                         # numbers
    ('{"status": 404}', '{"status": "404"}', True),
    ('{"status": "404"}', '{"status": 404}', True),
    ('{"status": 404}', '{"status": 4040}', False),
    ('{"user": ["root", "admin"]}', '{"user": "admin"}', True),           # list = any
    ('{"user": nil}', '{"ip": "x"}', True),                               # nil = missing
    ('{"user": nil}', '{"user": "x"}', False),
    ('{"ok": true}', '{"ok": true}', True),
    ('{"ok": false}', '{"ok": nil}', False),
    ('{"path|contains": "../"}', '{"path": "/a/../../etc"}', True),
    ('{"path|startswith": "/admin"}', '{"path": "/Admin/x"}', True),
    ('{"path|endswith": ".php"}', '{"path": "/x.php?a=1"}', False),
    ('{"path|contains|all": ["union", "select"]}', '{"path": "1 UNION SELECT"}', True),
    ('{"path|contains|all": ["union", "select"]}', '{"path": "1 UNION"}', False),
    ('{"bytes|gt": 1000}', '{"bytes": 1001}', True),
    ('{"bytes|gte": 1000}', '{"bytes": 999}', False),
    ('{"bytes|lt": "10"}', '{"bytes": "9"}', True),
    ('{"ip|cidr": "10.0.0.0/8"}', '{"ip": "10.2.3.4"}', True),
    ('{"ip|cidr": "10.0.0.0/8"}', '{"ip": "11.2.3.4"}', False),
    ('{"ip|cidr": "2001:db8::/32"}', '{"ip": "2001:db8::1"}', True),
    ('{"ip|cidr": "10.0.0.0/8"}', '{"ip": "not an ip"}', False),
    ('{"cmd|re": "(?i)-enc\\\\s"}', '{"cmd": "powershell -ENC abc"}', True),
    ('{"cmd|re|i": "-enc\\\\s"}', '{"cmd": "powershell -ENC abc"}', True),
    ('{"cmd|re": "-enc\\\\s"}', '{"cmd": "powershell -ENC abc"}', False),
    ('{"user|exists": true}', '{"user": "x"}', True),
    ('{"user|exists": false}', '{"user": ""}', True),
    ('{"geo.country": "NL"}', '{"geo": {"country": "nl"}}', True),        # dotted path
    ('{"EventID": 4625}', '{"eventid": 4625}', True),                     # field names ignore case
    ('{"tags|contains": "bad"}', '{"tags": ["good", "very bad"]}', True), # lists: any item
    ('{"cmd|windash|contains": " -enc "}', '{"cmd": "powershell /enc x"}', True),
])
def test_where_values_and_modifiers(where, record, expected):
    out = run(f'say(detect.match({{"name": "t", "where": {where}}}, {record}))')
    assert out == ["true" if expected else "false"]


def test_where_function_and_unless():
    src = '''let r = {"name": "big", "where": fn(rec) { return rec.bytes > 100 }, "unless": {"ip|cidr": "192.0.2.0/24"}}
say(detect.match(r, {"bytes": 500, "ip": "203.0.113.1"}), detect.match(r, {"bytes": 500, "ip": "192.0.2.9"}), detect.match(r, {"bytes": 5, "ip": "203.0.113.1"}))'''
    assert run(src) == ["true false false"]


# -- run(): thresholds, windows, by ---------------------------------------------------

def records_src(times, ip="203.0.113.7", extra=""):
    return "[" + ", ".join(f'{{"time": {t}, "ip": "{ip}", "action": "failed_login"{extra}}}' for t in times) + "]"


def test_run_simple_rule_one_alert_per_rule():
    src = f'''let alerts = detect.run({{"name": "Fail", "level": "low", "where": {{"action": "failed_login"}}}}, {records_src([10, 20, 30])})
say(len(alerts), alerts[0].rule, alerts[0].level, alerts[0].count, alerts[0].first, alerts[0].last, alerts[0].by, len(alerts[0].records))'''
    assert run(src) == ["1 Fail low 3 10 30 nil 3"]


def test_threshold_window_one_alert_per_burst():
    # burst 1: 5 events in 40s; gap; 2 events (too few); gap; burst 2: 6 events
    times = [0, 10, 20, 30, 40, 1000, 1010, 5000, 5005, 5010, 5015, 5020, 5025]
    rule = '{"name": "BF", "where": {"action": "failed_login"}, "count": 5, "within": 60, "by": "ip"}'
    src = f'''for a in detect.run({rule}, {records_src(times)}) {{ say(a.by, a.count, a.first, a.last) }}'''
    assert run(src) == ["203.0.113.7 5 0 40", "203.0.113.7 6 5000 5025"]


def test_threshold_not_reached_when_spread_out():
    rule = '{"name": "BF", "where": {"action": "failed_login"}, "count": 3, "within": 60}'
    assert run(f"say(detect.run({rule}, {records_src([0, 100, 200, 300])}))") == ["[]"]
    rule_no_window = '{"name": "BF", "where": {"action": "failed_login"}, "count": 3}'
    assert run(f"say(len(detect.run({rule_no_window}, {records_src([0, 100, 200, 300])})))") == ["1"]


def test_threshold_counts_repeats_and_groups_by_field():
    src = '''let rs = [
  {"time": 1, "ip": "a", "user": "x", "repeat": 4},
  {"time": 2, "ip": "b", "user": "x"},
  {"time": 3, "ip": "b", "user": "y"},
  {"time": 4, "user": "z"},
]
for a in detect.run({"name": "r", "where": {"user|exists": true}, "count": 4, "within": 10, "by": "ip"}, rs) { say(a.by, a.count) }
for a in detect.run({"name": "r", "where": {"user|exists": true}, "count": 1, "by": ["ip", "user"]}, rs) { say(a.by, a.by_field) }'''
    assert run(src) == ["a 4", "a / x ip / user", "b / x ip / user", "b / y ip / user"]


def test_alerts_sorted_by_level_then_time():
    src = '''let rules = [
  {"name": "low", "level": "low", "where": {"k": 1}},
  {"name": "crit", "level": "critical", "where": {"k": 2}},
  {"name": "high", "level": "high", "where": {"k": 1}},
]
let rs = [{"time": 5, "k": 1}, {"time": 9, "k": 2}]
say(detect.run(rules, rs).map(fn(a) { return a.rule }))
say(detect.run(rules[0], []))'''
    assert run(src) == ['["crit", "high", "low"]', "[]"]


def test_records_without_time_are_skipped_only_by_windows():
    src = '''let rs = [{"k": 1}, {"k": 1}]
say(len(detect.run({"name": "a", "where": {"k": 1}, "count": 2}, rs)), len(detect.run({"name": "a", "where": {"k": 1}, "count": 2, "within": 9}, rs)))'''
    assert run(src) == ["1 0"]


# -- built-in hunts -------------------------------------------------------------------------

def test_brute_force_with_success_after():
    src = '''let rs = []
for i in range(6) { rs.push({"time": 100 + i, "ip": "203.0.113.42", "user": ["root", "admin"][i % 2], "success": false, "action": "failed_login"}) }
rs.push({"time": 100, "ip": "203.0.113.42", "user": "root", "success": false, "action": "invalid_user"})
rs.push({"time": 200, "ip": "203.0.113.42", "user": "deploy", "success": true, "action": "login"})
rs.push({"time": 150, "ip": "192.0.2.1", "user": "amy", "success": false})
let a = detect.brute_force(rs)
say(len(a), a[0].by, a[0].count, a[0].level, a[0].users, a[0].success_after, a[0].success_user)
say(len(detect.brute_force(rs, 7)), len(detect.brute_force(rs, 6, 3)))
say(len(detect.failed_logins(rs)))'''
    assert run(src) == ['1 203.0.113.42 6 critical ["root", "admin"] true deploy', "0 0", "7"]


def test_brute_force_without_success_and_by_user_fallback():
    src = '''let rs = []
for i in range(5) { rs.push({"time": i, "user": "svc_sql", "success": false}) }
let a = detect.brute_force(rs)[0]
say(a.by, a.by_field, a.level, a.success_after)'''
    assert run(src) == ["svc_sql user high false"]


def test_brute_force_on_sample_auth_log(root):
    path = root / "apps" / "data" / "auth.log"
    src = f'''let a = detect.brute_force(logs.read("{path}"))
say(len(a), a[0].by, a[0].count, a[0].success_user, a[0].level)'''
    assert run(src) == ["1 203.0.113.42 22 deploy critical"]


def test_port_scan():
    src = '''let rs = []
for p in range(15) { rs.push({"time": 10 + p, "src_ip": "198.51.100.77", "dst_ip": "192.0.2.5", "dst_port": 20 + p}) }
for p in range(15) { rs.push({"time": 10 + p * 100, "src_ip": "192.0.2.9", "dst_port": 20 + p}) }
for p in range(30) { rs.push({"time": 10 + p, "src_ip": "192.0.2.8", "dst_port": 443}) }
let a = detect.port_scan(rs)
say(len(a), a[0].by, a[0].count, a[0].attempts, a[0].ports.slice(0, 3), a[0].targets)
say(len(detect.port_scan(rs, 16)), len(detect.port_scan(rs, 5, 1000)))'''
    assert run(src) == ['1 198.51.100.77 15 15 [20, 21, 22] ["192.0.2.5"]', "0 2"]


def test_port_scan_ignores_ssh_client_ports(root):
    path = root / "apps" / "data" / "auth.log"
    assert run(f'say(detect.port_scan(logs.read("{path}")))') == ["[]"]


def test_rare():
    src = '''let rs = [{"time": 1, "agent": "curl"}, {"time": 2, "agent": "Mozilla"}, {"time": 3, "agent": "Mozilla"}, {"time": 4, "agent": "sqlmap"}, {"time": 5}]
say(detect.rare(rs, "agent").map(fn(a) { return a.by }))
say(detect.rare(rs, "agent", 2).map(fn(a) { return [a.by, a.count] }), detect.rare(rs, "agent")[0].level)'''
    assert run(src) == ['["curl", "sqlmap"]', '[["curl", 1], ["sqlmap", 1], ["Mozilla", 2]] low']


# -- the YAML reader -------------------------------------------------------------------------

def test_yaml_subset():
    text = """# comment
title: 'It''s a rule'   # trailing comment
id: 123
ratio: 1.5
on: true
off: false
none: null
tilde: ~
quoted: "tab\\there \\"q\\" #not a comment"
url: http://x.example/a#frag
list:
  - a
  - 'b: c'
  - 3
nested:
  inner:
    deep: x
same_indent_list:
- one
- two
maps:
  - name: a
    value: 1
  - name: b
    value: [x, 'y, z', 2]
flow: {k: v, n: 2}
empty_list: []
block: |
  line one
    indented

  line three
folded: >-
  joined
  words

  para
after: done
"""
    data = read_yaml(text)
    assert data == {
        "title": "It's a rule", "id": 123, "ratio": 1.5, "on": True, "off": False, "none": None, "tilde": None,
        "quoted": 'tab\there "q" #not a comment', "url": "http://x.example/a#frag",
        "list": ["a", "b: c", 3], "nested": {"inner": {"deep": "x"}}, "same_indent_list": ["one", "two"],
        "maps": [{"name": "a", "value": 1}, {"name": "b", "value": ["x", "y, z", 2]}],
        "flow": {"k": "v", "n": 2}, "empty_list": [],
        "block": "line one\n  indented\n\nline three\n", "folded": "joined words\npara", "after": "done",
    }


def test_yaml_multiline_plain_and_quoted_values():
    assert read_yaml("description: a long\n  wrapped line\nx: 'one\n  two'\n") == {
        "description": "a long wrapped line", "x": "one two"}


@pytest.mark.parametrize("text,message", [
    ("a: *nowhere", "alias *nowhere points to an anchor"),
    ("a: !!date 1", "tag !!date isn't supported"),
    ("? complex\n: key", "complex keys"),
    ("a: 1\na: 2", 'the key "a" appears twice'),
    ("a:\n\tb: 1", "spaces, not tabs"),
    ("a: 1\n   b: 2", "indented"),
    ("a: [1, [2]", "never closes"),
    ("a: 1\n---\nb: 2", "2 YAML documents"),
    ("a: 1\nb", "key: value"),
    ("a: 'never closed", "never ends"),
])
def test_yaml_errors(text, message):
    with pytest.raises(YamlError) as info:
        read_yaml(text)
    assert message in str(info.value)


def test_parse_yaml_from_tb():
    assert run('say(detect.parse_yaml("a: 1\\nb:\\n  - x\\n  - y"))') == ['{"a": 1, "b": ["x", "y"]}']
    assert "That YAML doesn't work: the alias *x" in str(tb_error('import detect\ndetect.parse_yaml("a: *x")'))


# -- Sigma ---------------------------------------------------------------------------------

SIGMA = """title: Suspicious Things
level: high
logsource:
  product: windows
detection:
  selection_img:
    - Image|endswith: '\\powershell.exe'
    - Image|endswith: '\\pwsh.exe'
  selection_cli:
    CommandLine|contains:
      - ' -enc '
      - ' -EncodedCommand '
  filter:
    User: 'SYSTEM'
  condition: all of selection_* and not filter
"""


def sigma_match(rule_text, record_src):
    path = "rule.yml"
    with open(path, "w") as f:
        f.write(rule_text)
    return run(f'say(detect.match(detect.sigma("{path}"), {record_src}))')[0] == "true"


def test_sigma_rule_fields():
    with open("r.yml", "w") as f:
        f.write(SIGMA)
    out = run('let r = detect.sigma("r.yml")\nsay(r.name, r.level, r.formats, r.count, r.source, r.logsource)')
    assert out == ['Suspicious Things high ["windows"] 1 sigma {"product": "windows"}']


@pytest.mark.parametrize("record,expected", [
    ('{"format": "windows", "Image": "C:\\\\Windows\\\\powershell.exe", "CommandLine": "powershell.exe -enc AAA", "User": "bob"}', True),
    ('{"format": "windows", "Image": "C:\\\\x\\\\pwsh.exe", "CommandLine": "pwsh -EncodedCommand AAA "}', True),
    ('{"format": "windows", "Image": "C:\\\\x\\\\pwsh.exe", "CommandLine": "pwsh -EncodedCommand AAA", "User": "system"}', False),
    ('{"format": "windows", "Image": "C:\\\\x\\\\cmd.exe", "CommandLine": "x -enc y"}', False),
    ('{"format": "windows", "Image": "C:\\\\x\\\\powershell.exe", "CommandLine": "powershell -File a.ps1"}', False),
    ('{"format": "auth", "Image": "C:\\\\Windows\\\\powershell.exe", "CommandLine": "powershell.exe -enc AAA"}', False),
    ('{"Image": "C:\\\\Windows\\\\powershell.exe", "CommandLine": "powershell.exe -enc AAA"}', True),
])
def test_sigma_conditions_and_logsource(record, expected):
    assert sigma_match(SIGMA, record) is expected


@pytest.mark.parametrize("condition,record,expected", [
    ("a or b", '{"x": 1}', True),
    ("a and b", '{"x": 1}', False),
    ("a and b", '{"x": 1, "y": 2}', True),
    ("not a", '{"x": 1}', False),
    ("not (a or b)", '{"z": 1}', True),
    ("1 of them", '{"y": 2}', True),
    ("all of them", '{"y": 2}', False),
    ("any of b*", '{"y": 2}', True),
    ("a and not b or c", '{"message": "evil thing"}', True),
    ("a and (not b or c)", '{"x": 1, "y": 2}', False),
    ("c", '{"message": "pure EVIL here"}', True),
    ("c", '{"raw": "evilness"}', True),
])
def test_sigma_boolean_logic(condition, record, expected):
    text = f"""title: t
detection:
  a:
    x: 1
  b:
    y: 2
  c:
    - evil
  condition: {condition}
"""
    assert sigma_match(text, record) is expected


def test_sigma_list_of_maps_is_or_and_condition_list():
    text = """title: t
detection:
  sel:
    - user: root
    - user: admin
      ip: 10.0.0.1
  other:
    port: 22
  condition:
    - sel
    - other
"""
    assert sigma_match(text, '{"user": "root"}')
    assert sigma_match(text, '{"user": "admin", "ip": "10.0.0.1"}')
    assert not sigma_match(text, '{"user": "admin"}')
    assert sigma_match(text, '{"port": 22}')


def test_sigma_null_and_numbers_and_re():
    text = """title: t
detection:
  sel:
    EventID: 4625
    TargetUserName|re: '^adm.*'
    Extra: null
  condition: sel
"""
    assert sigma_match(text, '{"EventID": 4625, "TargetUserName": "administrator"}')
    assert sigma_match(text, '{"EventID": "4625", "TargetUserName": "admin", "Extra": ""}')
    assert not sigma_match(text, '{"EventID": 4625, "TargetUserName": "administrator", "Extra": "x"}')
    assert not sigma_match(text, '{"EventID": 4624, "TargetUserName": "admin"}')


def test_sigma_count_by_and_timeframe():
    text = """title: brute
level: medium
detection:
  sel:
    action: failed_login
  condition: sel | count() by ip > 3
timeframe: 1m
"""
    with open("bf.yml", "w") as f:
        f.write(text)
    src = '''let r = detect.sigma("bf.yml")
say(r.count, r.within, r.by)
let rs = []
for i in range(4) { rs.push({"time": i, "ip": "a", "action": "failed_login"}) }
for i in range(3) { rs.push({"time": i, "ip": "b", "action": "failed_login"}) }
for i in range(4) { rs.push({"time": i * 100, "ip": "c", "action": "failed_login"}) }
say(detect.run(r, rs).map(fn(a) { return [a.by, a.count] }))'''
    assert run(src) == ["4 60 ip", '[["a", 4]]']


def test_sigma_from_text_and_sample_rules(root):
    out = run('let r = detect.sigma("title: inline\\ndetection:\\n  s:\\n    a: 1\\n  condition: s")\nsay(r.name, r.level, r.formats)')
    assert out == ["inline medium nil"]
    folder = root / "apps" / "data" / "sigma"
    out = run(f'say(detect.sigma_folder("{folder}").map(fn(r) {{ return r.name }}))')
    assert out == ['["SSH Brute Force From One Address", "Web Attack Attempts (Path Traversal or SQL Injection)", '
                   '"Encoded PowerShell Command Line", "New Local Account Created"]']


def test_sample_sigma_rules_find_the_sample_attacks(root):
    data = root / "apps" / "data"
    src = f'''let rules = detect.sigma_folder("{data / 'sigma'}")
for f in ["auth.log", "access.log", "windows_security.json", "firewall.log"] {{
  say(f, detect.run(rules, logs.read("{data}/" + f)).map(fn(a) {{ return [a.rule, a.by, a.count] }}))
}}'''
    out = run(src)
    assert out[0] == 'auth.log [["SSH Brute Force From One Address", "203.0.113.42", 22]]'
    assert out[1] == 'access.log [["Web Attack Attempts (Path Traversal or SQL Injection)", nil, 21]]'
    assert out[2] == ('windows_security.json [["Encoded PowerShell Command Line", nil, 1], '
                      '["New Local Account Created", nil, 1]]')
    assert out[3] == "firewall.log []"


@pytest.mark.parametrize("text,message", [
    ("title: t\nlevel: high", "has no detection: section"),
    ("title: t\ndetection:\n  s:\n    a: 1", "needs a condition"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s and t", 'uses "t", but there\'s no selection with that name (selections: s)'),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s and", "ends too early"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: (s", "never closed"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s*", "only work after '1 of'"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: 1 of x*", 'no selection matches "x*"'),
    ("title: t\ndetection:\n  s:\n    a|utf16le: x\n  condition: s", "|utf16le needs |base64 or |base64offset after it"),
    ("title: t\ndetection:\n  s:\n    a|sparkle: x\n  condition: s", "I don't know the modifier |sparkle"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s | median(x) > 5", "I can't read the aggregation \"| median(x) > 5\""),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s | max() > 5", "max() needs a field"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s\ntimeframe: soon", "timeframe \"soon\" should look like 30s"),
    ("title: t\nlevel: urgent\ndetection:\n  s:\n    a: 1\n  condition: s", "level should be one of"),
    ("title: t\ndetection:\n  s: []\n  condition: s", "is empty"),
    ("title: t\ndetection:\n  s:\n    - a: 1\n    - x\n  condition: s", "mixes maps and plain values"),
    ("title: t\ndetection:\n  s:\n    a|re: '('\n  condition: s", "doesn't work"),
    ("title: t\ndetection:\n  s:\n    a: *x\n  condition: s", "I couldn't read the Sigma rule 'r.yml': the alias *x"),
])
def test_sigma_errors(text, message):
    with open("r.yml", "w") as f:
        f.write(text)
    err = tb_error('import detect\ndetect.sigma("r.yml")')
    assert message in str(err)


@pytest.mark.parametrize("src,message", [
    ('detect.rule({"where": {"a": 1}})', 'every rule needs a "name"'),
    ('detect.rule({"name": "x"})', 'needs a "where" map'),
    ('detect.rule({"name": "x", "where": {"a": 1}, "level": "loud"})', "level should be one of informational, low, medium, high, critical"),
    ('detect.rule({"name": "x", "where": {"a": 1}, "count": 0})', "count should be a whole number of 1 or more"),
    ('detect.rule({"name": "x", "where": {"a": 1}, "within": -5})', "within should be a number of seconds"),
    ('detect.rule({"name": "x", "where": {"a": 1}, "by": 5})', "by should be a field name"),
    ('detect.rule({"name": "x", "where": 5})', "where should be a map of field: value, or a function"),
    ('detect.rule({"name": "x", "where": {"a|gt": "lots"}})', "|gt needs a number"),
    ('detect.rule({"name": "x", "where": {"a|cidr": "nope"}})', '"nope" isn\'t a network'),
    ('detect.rule({"name": "x", "where": {"a": []}})', "the list of values for a is empty"),
    ('detect.run(5, [])', "detect.run() needs rules to be a list or a map"),
    ('detect.run([{"name": "x"}], [])', 'needs a "where" map'),
    ('detect.brute_force([], 0)', "needs attempts of 1 or more"),
    ('detect.port_scan([], 1)', "needs ports of 2 or more"),
    ('detect.sigma("missing.yml")', "I couldn't find the Sigma rule 'missing.yml'"),
    ('detect.sigma_folder("nope")', "I couldn't find the folder 'nope'."),
])
def test_detect_errors(src, message):
    assert message in str(tb_error(f"import detect\n{src}"))


def test_rule_errors_are_catchable():
    assert run('try { detect.rule({}) } catch e { say("caught", e.kind) }') == ["caught error"]


# -- Sigma 3.0: every modifier, richer conditions, aggregations, near, correlations ----------

import base64 as _b64  # noqa: E402
import json as _json  # noqa: E402


def sigma3_match(detection_yaml, record, extra=""):
    text = "title: t\ndetection:\n" + detection_yaml + extra
    src = f"let r = detect.sigma({_json.dumps(text, ensure_ascii=False)})\nsay(detect.match(r, {_json.dumps(record, ensure_ascii=False)}))"
    return run(src) == ["true"]


def b64(text, enc="utf-8"):
    return _b64.b64encode(text.encode(enc)).decode()


@pytest.mark.parametrize("selection,record,expected", [
    ("    cmd|base64: 'whoami'", {"cmd": b64("whoami")}, True),
    ("    cmd|base64|contains: 'whoami'", {"cmd": "powershell -enc " + b64("whoami")}, True),
    ("    cmd|base64offset|contains: 'http://'", {"cmd": "x " + b64("xx http://evil")[4:]}, True),
    ("    cmd|base64offset|contains: 'http://'", {"cmd": b64("ab http://e")}, True),
    ("    cmd|utf16le|base64offset|contains: 'Invoke'", {"cmd": "-enc " + b64("IEX; Invoke-Thing", "utf-16-le")}, True),
    ("    cmd|wide|base64offset|contains: 'Invoke'", {"cmd": b64("abc Invoke", "utf-16-le")}, True),
    ("    cmd|utf16be|base64|contains: 'hi'", {"cmd": b64("hi", "utf-16-be")}, True),
    ("    cmd|windash|contains: ' -enc '", {"cmd": "powershell /enc AAA"}, True),
    ("    cmd|windash|contains: ' -enc '", {"cmd": "powershell \u2013enc AAA"}, True),
    ("    ip|cidr: '10.0.0.0/8'", {"ip": "10.20.30.40"}, True),
    ("    ip|cidr:\n      - '192.168.0.0/16'\n      - '2001:db8::/32'", {"ip": "2001:db8::5"}, True),
    ("    ip|cidr: '10.0.0.0/8'", {"ip": "11.0.0.1"}, False),
    ("    msg|re|i: '^hello.*WORLD$'", {"msg": "HELLO big world"}, True),
    ("    msg|re: '^hello'", {"msg": "HELLO"}, False),
    ("    msg|re|m: '^two$'", {"msg": "one\ntwo"}, True),
    ("    msg|re|s: 'a.b'", {"msg": "a\nb"}, True),
    ("    bytes|gt: 1000", {"bytes": 5000}, True),
    ("    bytes|lte: 1000", {"bytes": 5000}, False),
    ("    bytes|gte: 10", {"bytes": "12"}, True),
    ("    bytes|lt: 10", {"bytes": 3.5}, True),
    ("    user|exists: true", {"user": "x"}, True),
    ("    user|exists: false", {"user": "x"}, False),
    ("    user|exists: false", {"other": 1}, True),
    ("    cmd|contains|all:\n      - 'net'\n      - 'user'\n      - '/add'", {"cmd": "net user bob /add"}, True),
    ("    cmd|contains|all:\n      - 'net'\n      - '/add'", {"cmd": "net user bob"}, False),
    ("    src|fieldref: dst", {"src": "a", "dst": "A"}, True),
    ("    src|fieldref: dst", {"src": "a", "dst": "b"}, False),
    ("    path|fieldref|startswith: home", {"path": "/home/t/x", "home": "/home/t"}, True),
    ("    user|cased: Admin", {"user": "admin"}, False),
])
def test_sigma_modifiers(selection, record, expected):
    assert sigma3_match("  sel:\n" + selection + "\n  condition: sel", record) is expected


def test_sigma_expand_placeholders():
    text = "title: t\ndetection:\n  sel:\n    user|expand: '%admins%'\n  condition: sel"
    out = run(f'let r = detect.sigma({_json.dumps(text)}, {{"placeholders": {{"admins": ["root", "tanisha"]}}}})\n'
              'say(detect.match(r, {"user": "tanisha"}), detect.match(r, {"user": "bob"}))')
    assert out == ["true false"]


@pytest.mark.parametrize("condition,record,expected", [
    ("all of them", {"a": 1, "b": 2, "c": 3}, True),
    ("all of them", {"a": 1, "b": 2}, False),
    ("1 of them", {"c": 3}, True),
    ("1 of selection_*", {"b": 2}, True),
    ("1 of selection_*", {"c": 3}, False),
    ("all of selection_*", {"a": 1, "b": 2}, True),
    ("all of selection_* and not filter", {"a": 1, "b": 2, "c": 3}, False),
    ("not filter", {"a": 1}, True),
    ("(selection_a or filter) and not selection_b", {"c": 3}, True),
])
def test_sigma_condition_forms(condition, record, expected):
    det = f"  selection_a:\n    a: 1\n  selection_b:\n    b: 2\n  filter:\n    c: 3\n  condition: {condition}"
    assert sigma3_match(det, record) is expected


EVENTS = [
    {"time": 1000 + i * 10, "ip": "203.0.113.42" if i < 6 else "192.0.2.9", "user": f"u{i % 4}", "bytes": 100 * (i + 1),
     "action": "failed_login"} for i in range(9)
]


def run_sigma(text, records=EVENTS):
    return run(f"let r = detect.sigma_all({_json.dumps(text)})\nlet a = detect.run(r, {_json.dumps(records)})\n"
               "say(len(a))\nfor x in a { say(x.by, x.count, x.get(\"value\", nil)) }")


@pytest.mark.parametrize("agg,expected", [
    ("count() by ip > 5", ["1", "203.0.113.42 6 nil"]),
    ("count(user) by ip >= 4", ["1", "203.0.113.42 4 4"]),
    ("count(user) by ip > 4", ["0"]),
    ("max(bytes) by ip > 800", ["1", "192.0.2.9 3 900"]),
    ("min(bytes) by ip < 200", ["1", "203.0.113.42 6 100"]),
    ("sum(bytes) > 4000", ["1", "nil 9 4500"]),
    ("avg(bytes) by ip >= 800", ["1", "192.0.2.9 3 800"]),
])
def test_sigma_aggregations(agg, expected):
    text = f"title: agg\ndetection:\n  sel:\n    action: failed_login\n  condition: sel | {agg}\n"
    assert run_sigma(text) == expected


def test_sigma_near():
    records = [{"time": 100, "action": "failed_login", "ip": "x"}, {"time": 130, "action": "login", "ip": "x"},
               {"time": 5000, "action": "login", "ip": "y"}]
    text = ("title: success after failure\ndetection:\n  ok:\n    action: login\n  bad:\n    action: failed_login\n"
            "  condition: ok | near bad\ntimeframe: 1m\n")
    out = run(f"let a = detect.run(detect.sigma({_json.dumps(text)}), {_json.dumps(records)})\nsay(len(a), a[0].count)")
    assert out == ["1 2"]


CORR_BASE = """title: failed
name: failed
logsource:
  product: linux
  service: auth
detection:
  sel:
    action: failed_login
  condition: sel
---
title: success
name: success
detection:
  sel:
    action: login
  condition: sel
---
"""

CORR_RECORDS = (
    [{"time": 100 + i, "action": "failed_login", "ip": "203.0.113.42", "user": f"u{i}"} for i in range(6)]
    + [{"time": 150, "action": "login", "ip": "203.0.113.42", "user": "u1"}]
    + [{"time": 100, "action": "login", "ip": "192.0.2.9", "user": "t"},
       {"time": 110, "action": "failed_login", "ip": "192.0.2.9", "user": "t"}]
)


@pytest.mark.parametrize("corr,expected", [
    ("type: event_count\n  rules: [failed]\n  group-by: [ip]\n  timespan: 5m\n  condition:\n    gte: 5", ["203.0.113.42 6"]),
    ("type: value_count\n  rules: [failed]\n  group-by: [ip]\n  timespan: 5m\n  condition:\n    field: user\n    gt: 3",
     ["203.0.113.42 6"]),
    ("type: temporal\n  rules: [failed, success]\n  group-by: [ip]\n  timespan: 2m",
     ["192.0.2.9 2", "203.0.113.42 7"]),
    ("type: temporal_ordered\n  rules: [failed, success]\n  group-by: [ip]\n  timespan: 2m", ["203.0.113.42 7"]),
])
def test_sigma_correlation_types(corr, expected):
    text = CORR_BASE + "title: corr\nlevel: critical\ncorrelation:\n  " + corr + "\n"
    out = run(f"let rules = detect.sigma_all({_json.dumps(text)})\n"
              f"let a = detect.run(rules.filter(fn(r) {{ return r.has(\"correlation\") }}), {_json.dumps(CORR_RECORDS)})\n"
              "for x in a.sort_by(fn(x) { return x.by }) { say(x.by, x.count) }")
    assert out == expected


def test_sigma_correlation_hides_building_blocks_and_errors():
    text = CORR_BASE + "title: corr\ncorrelation:\n  type: event_count\n  rules: [failed]\n  group-by: [ip]\n  timespan: 5m\n  condition:\n    gte: 5\n"
    out = run(f"let rules = detect.sigma_all({_json.dumps(text)})\nsay(rules.map(fn(r) {{ return r.name + \":\" + str(r.get(\"hidden\", false)) }}))")
    assert out == ['["corr:false", "failed:true", "success:false"]']
    bad = CORR_BASE + "title: c\ncorrelation:\n  type: event_count\n  rules: [nope]\n  condition:\n    gte: 1\n"
    assert 'refers to the rule "nope"' in str(tb_error(f"import detect\ndetect.sigma_all({_json.dumps(bad)})"))
    bad = CORR_BASE + "title: c\ncorrelation:\n  type: temporal\n  rules: [failed, success]\n"
    assert "needs a timespan" in str(tb_error(f"import detect\ndetect.sigma_all({_json.dumps(bad)})"))


def test_sigma_fields_and_logsource_mapping():
    text = ("title: t\nlogsource:\n  product: windows\n  service: security\nfields:\n  - TargetUserName\n"
            "detection:\n  sel:\n    EventID: 4625\n  condition: sel\n")
    out = run(f'let r = detect.sigma({_json.dumps(text)})\nsay(r.formats, r.fields)\n'
              'let a = detect.run(r, [{"format": "windows", "EventID": 4625, "TargetUserName": "tanisha", "time": 1}, '
              '{"format": "auth", "EventID": 4625}])\nsay(len(a), a[0].fields)\n'
              'say(detect.logsource({"product": "linux", "service": "auth"}).kql)')
    assert out == ['["windows"] ["TargetUserName"]', '1 {"TargetUserName": "tanisha"}', "Syslog"]


BRUTE = """title: SSH brute force
logsource:
  product: linux
  service: auth
detection:
  selection:
    action: failed_login
  filter:
    ip|cidr: 10.0.0.0/8
  condition: selection and not filter | count() by ip > 10
timeframe: 5m
"""


def test_sigma_to_query_backends():
    out = run(f"let r = detect.sigma({_json.dumps(BRUTE)})\nsay(detect.sigma_to_query(r, \"splunk\"))\n"
              "say(detect.sigma_to_query(r, \"kql\"))\nsay(detect.sigma_to_query(r, \"sentinel\") == detect.sigma_to_query(r, \"kql\"))")
    assert out[0] == ('sourcetype="linux_secure" action="failed_login" NOT cidrmatch("10.0.0.0/8", src) '
                      '| bin _time span=300s | stats count by _time, src | where count > 10') or \
        out[0].startswith('sourcetype="linux_secure" action="failed_login"')
    assert "| stats count by" in out[0] and "where count > 10" in out[0]
    assert out[1].startswith('Syslog\n| where Facility in ("auth", "authpriv")\n| extend Action = case(')
    assert '| where Action =~ "failed_login" and not(ipv4_is_in_range(SrcIp, "10.0.0.0/8"))' in out[1]
    assert "| summarize count_ = count() by SrcIp, bin(TimeGenerated, 300s)\n| where count_ > 10" in out[1]
    assert out[-1] == "true"
    err = str(tb_error(f"import detect\ndetect.sigma_to_query(detect.sigma({_json.dumps(BRUTE)}), \"elastic\")"))
    assert "can't count or group" in err


SYSLOG_COLUMNS = {"TimeGenerated", "Computer", "HostName", "HostIP", "Facility", "SeverityLevel", "ProcessName",
                  "ProcessID", "SyslogMessage", "EventTime"}


def kql_columns(query):
    """Column names a KQL query reads: identifiers outside strings that aren't keywords/functions."""
    import re
    text = re.sub(r'@?"(?:[^"\\]|\\.)*"', '""', query)
    made = set(re.findall(r"\| extend (\w+) =", text)) | {"count_"}
    words = set(re.findall(r"\b[A-Za-z_]\w*\b", text)) - made
    keywords = {"Syslog", "where", "extend", "summarize", "by", "in", "and", "or", "not", "case", "extract", "toint",
                "matches", "regex", "contains", "startswith", "count", "bin", "s", "ipv4_is_in_range", "coalesce"}
    return {w for w in words - keywords if not w.isdigit()}


def test_sigma_to_query_kql_ssh_brute_force_fits_the_syslog_schema():
    from conftest import ROOT
    path = (ROOT / "apps" / "data" / "sigma" / "ssh_brute_force.yml").as_posix()
    out = run_tb(f'import detect\nlet r = detect.sigma("{path}")\nsay(detect.sigma_to_query(r, "kql"))\n'
                 'say("----")\nsay(detect.sigma_to_query(r, "splunk"))')
    cut = out.index("----")
    kql, splunk = "\n".join(out[:cut]), "\n".join(out[cut + 1:])
    # Group-by and filter fields go through the field map; only real Syslog columns are read.
    assert kql_columns(kql) <= SYSLOG_COLUMNS, kql_columns(kql) - SYSLOG_COLUMNS
    assert "by ip" not in kql and "action =~" not in kql
    assert '| extend SrcIp = extract(@"(?:from |rhost=|by )' in kql
    assert 'SyslogMessage matches regex @"Failed \\S+ for ", "failed_login"' in kql
    assert kql.endswith('| where Action =~ "failed_login"\n| summarize count_ = count() by SrcIp, '
                        'bin(TimeGenerated, 300s)\n| where count_ > 10')
    assert splunk == ('sourcetype="linux_secure" action="failed_login" | bin _time span=300s | stats count by _time, src '
                      '| where count > 10')


def kql_extract(expression, message):
    """Run a SYSLOG_PARSED extract() / coalesce(extract(), ...) on one SyslogMessage with Python's re."""
    import re
    for pattern in re.findall(r'extract\(@"((?:[^"]|"")*)", 1, SyslogMessage\)', expression):
        m = re.search(pattern.replace('""', '"'), message)  # @"..." is a KQL verbatim string
        if m and m.group(1):
            return m.group(1)  # coalesce: the first non-empty one
    return None


@pytest.mark.parametrize("message", [
    "Failed password for invalid user admin from 203.0.113.5 port 52814 ssh2",
    "Failed password for root from 2001:db8::7 port 22 ssh2",
    "Accepted publickey for alice from 198.51.100.2 port 40022 ssh2",
    "Invalid user oracle from 203.0.113.9 port 4242",
    "pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost=203.0.113.5  user=root",
    "pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost=host.dead.example",
    "pam_unix(sudo:session): session opened for user root(uid=0) by alice(uid=1000)",
    "  alice : TTY=pts/0 ; PWD=/ ; USER=root ; COMMAND=/bin/ls",
    "  bob : 3 incorrect password attempts ; TTY=pts/1 ; PWD=/home/bob ; USER=root ; COMMAND=/bin/sh",
    "Connection closed by authenticating user bob 203.0.113.5 port 22 [preauth]",
    "Connection closed by invalid user x 203.0.113.5 port 22",
    "Connection closed by 203.0.113.5 port 22 [preauth]",
    "Disconnected from user bob 203.0.113.5 port 22",
    "Received disconnect from 203.0.113.5 port 22:11: Bye Bye [preauth]",
])
def test_kql_syslog_extracts_match_logs(message):
    from tblib.detect import SYSLOG_PARSED
    from tblib.logs import LineParser
    record = LineParser("auth").parse("Oct 31 10:00:00 web1 sshd[812]: " + message)
    assert kql_extract(SYSLOG_PARSED["UserName"], message) == (record.get("user") or None)
    ip = record.get("ip") or None
    if ip and not any(c.isdigit() for c in ip):
        ip = None  # logs keeps rhost=host.dead.example as given; SrcIp is only ever an address
    assert kql_extract(SYSLOG_PARSED["SrcIp"], message) == ip
    port = kql_extract(SYSLOG_PARSED["SrcPort"], message)
    assert (int(port) if port else None) == record.get("port")


def test_sigma_to_query_kql_syslog_only_parses_what_it_uses():
    text = ("title: t\nlogsource:\n  product: linux\n  service: sshd\ndetection:\n  sel:\n    user: root\n"
            "    message|contains: Accepted\n  condition: sel\n")
    lines = run(f"say(detect.sigma_to_query(detect.sigma({_json.dumps(text)}), \"kql\"))")
    kql = "\n".join(lines)
    assert kql.splitlines()[1] == '| where Facility in ("auth", "authpriv") and ProcessName startswith "sshd"'
    assert "| extend UserName = coalesce(" in kql and "Action" not in kql and "SrcIp" not in kql
    assert kql.endswith('| where UserName =~ "root" and SyslogMessage contains "Accepted"')
    assert kql_columns(kql) <= SYSLOG_COLUMNS
    # A table given by hand drops the logsource's Facility filter.
    lines = run(f"say(detect.sigma_to_query(detect.sigma({_json.dumps(text)}), \"kql\", {{\"table\": \"MySyslog\"}}))")
    assert "\n".join(lines) == 'MySyslog\n| where user =~ "root" and message contains "Accepted"'


@pytest.mark.parametrize("backend,expected", [
    ("splunk", 'source="WinEventLog:Security" EventCode=4688 (CommandLine="*-enc*" OR CommandLine="*/enc*")'),
    ("elastic", "(winlog.channel:Security) AND (event.code:4688 AND (process.command_line:*\\-enc* OR "
                "process.command_line:*\\/enc*))"),
    ("kql", "SecurityEvent\n| where EventID == 4688"),
])
def test_sigma_to_query_field_mapping(backend, expected):
    text = ("title: t\nlogsource:\n  product: windows\n  service: security\ndetection:\n  sel:\n    EventID: 4688\n"
            "    CommandLine|contains:\n      - '-enc'\n      - '/enc'\n  condition: sel\n")
    [out] = run(f"say(detect.sigma_to_query(detect.sigma({_json.dumps(text)}), \"{backend}\"))")
    assert out.startswith(expected) or expected in out, out


def test_sigma_to_query_options_and_errors():
    out = run('let r = detect.rule({"name": "x", "where": {"user": "root", "path|contains": "admin"}})\n'
              'say(detect.sigma_to_query(r, "splunk", {"index": "web", "fields": {"user": "usr"}}))\n'
              'say(detect.sigma_to_query(r, "elastic"))')
    assert 'usr="root"' in out[0] and "index=" in out[0] and 'path="*admin*"' in out[0]
    assert "user.name:root" in out[1] and "*admin*" in out[1]
    assert "doesn't know the backend 'qradar'" in str(tb_error('import detect\ndetect.sigma_to_query({"name": "x", "where": {"a": 1}}, "qradar")'))
    assert "doesn't know the option" in str(tb_error('import detect\ndetect.sigma_to_query({"name": "x", "where": {"a": 1}}, "kql", {"zz": 1})'))
