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
    ("a: &anchor 1\nb: *anchor", "anchors"),
    ("a: !!str 1", "tags"),
    ("? complex\n: key", "complex keys"),
    ("a: 1\na: 2", 'the key "a" appears twice'),
    ("a:\n\tb: 1", "spaces, not tabs"),
    ("a: 1\n   b: 2", "indented"),
    ("a: [1, [2]]", "inside [...]"),
    ("a: 1\n---\nb: 2", "2 YAML documents"),
    ("just text", "key: value"),
    ("a: 'never closed", "never ends"),
])
def test_yaml_errors(text, message):
    with pytest.raises(YamlError) as info:
        read_yaml(text)
    assert message in str(info.value)


def test_parse_yaml_from_tb():
    assert run('say(detect.parse_yaml("a: 1\\nb:\\n  - x\\n  - y"))') == ['{"a": 1, "b": ["x", "y"]}']
    assert "That YAML doesn't work: YAML aliases" in str(tb_error('import detect\ndetect.parse_yaml("a: *x")'))


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
    ("title: t\ndetection:\n  s:\n    a|base64offset|contains: x\n  condition: s", "|base64offset modifier isn't supported yet"),
    ("title: t\ndetection:\n  s:\n    a|sparkle: x\n  condition: s", "I don't know the modifier |sparkle"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s | count(user) by ip > 5", "count(user) (counting distinct values) isn't supported"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s | max(x) > 5", "only \"| count() by field > N\" aggregations"),
    ("title: t\ndetection:\n  s:\n    a: 1\n  condition: s\ntimeframe: soon", "timeframe \"soon\" should look like 30s"),
    ("title: t\nlevel: urgent\ndetection:\n  s:\n    a: 1\n  condition: s", "level should be one of"),
    ("title: t\ndetection:\n  s: []\n  condition: s", "is empty"),
    ("title: t\ndetection:\n  s:\n    - a: 1\n    - x\n  condition: s", "mixes maps and plain values"),
    ("title: t\ndetection:\n  s:\n    a|re: '('\n  condition: s", "doesn't work"),
    ("title: t\ndetection:\n  s:\n    a: &x 1\n  condition: s", "I couldn't read the Sigma rule 'r.yml': YAML anchors"),
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
