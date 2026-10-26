"""attack library: the bundled curated ATT&CK table, lookups, alert mapping, and loading the official STIX file."""

import json

import pytest

from conftest import ROOT, run_tb, tb_error
from tblib import attack


def run(source):
    return run_tb("import attack\n" + source)


def test_bundled_table_is_curated_and_well_formed():
    data = json.loads((ROOT / "tblib" / "data" / "attack.json").read_text(encoding="utf-8"))
    assert data["snapshot"] == "approximate, curated"
    ids = [t["id"] for t in data["techniques"]]
    assert len(ids) == len(set(ids)) == data["count"] >= 550
    shortnames = {t["shortname"] for t in data["tactics"]}
    assert len(shortnames) == 14
    for t in data["techniques"]:
        assert t["name"] and t["description"] and t["platforms"], t["id"]
        assert set(t["tactics"]) <= shortnames, t["id"]
        if "." in t["id"]:
            assert t["parent"] in ids


@pytest.mark.parametrize("tid,name", [
    ("T1110", "Brute Force"), ("T1110.003", "Password Spraying"), ("T1059.001", "PowerShell"),
    ("T1003.001", "LSASS Memory"), ("T1566.001", "Spearphishing Attachment"), ("T1486", "Data Encrypted for Impact"),
    ("T1021.001", "Remote Desktop Protocol"), ("T1190", "Exploit Public-Facing Application"),
    ("T1078", "Valid Accounts"), ("T1055.012", "Process Hollowing"), ("T1558.003", "Kerberoasting"),
])
def test_well_known_ids(tid, name):
    assert attack.default_table().techniques[tid]["name"] == name


def test_technique_lookup_forms():
    out = run('let t = attack.technique("attack.t1110.001")\nsay(t.id, t.full_name, t.parent, t.tactics, t.url)')
    assert out == ['T1110.001 Brute Force: Password Guessing T1110 ["credential-access"] '
                   'https://attack.mitre.org/techniques/T1110/001/']
    assert run('say(attack.technique("t1110").sub_techniques)') == ['["T1110.001", "T1110.002", "T1110.003", "T1110.004"]']
    assert run('say(attack.technique("T9999"))') == ["nil"]
    assert "needs a technique id" in str(tb_error('import attack\nattack.technique("brute")'))


def test_search_ranks_names_first():
    out = run('say(attack.search("brute").map(fn(t) { return t.id }))')
    assert out[0].startswith('["T1110", "T1110.001"')
    assert run('say(len(attack.search("password spraying")), attack.search("kerberoast", 1)[0].id)') == ["1 T1558.003"]
    assert run('say(attack.search("   "))') == ["[]"]


def test_tactic_names_and_platform_filter():
    out = run('let a = attack.tactic("credential_access")\nlet b = attack.tactic("TA0006")\nlet c = attack.tactic("Credential Access")\n'
              'say(len(a) == len(b), len(b) == len(c), a.map(fn(t) { return t.id }).contains("T1110"))')
    assert out == ["true true true"]
    assert run('say(attack.tactic("execution", "macOS").map(fn(t) { return t.id }).contains("T1047"))') == ["false"]
    assert "doesn't know the tactic 'sparkle'" in str(tb_error('import attack\nattack.tactic("sparkle")'))
    assert run('let t = attack.tactics()\nsay(len(t), t[0].shortname, t[13].shortname)') == ["14 reconnaissance impact"]


def test_map_alerts_by_tags():
    src = '''
let alerts = [
  {"rule": "SSH brute force", "level": "high", "tags": ["attack.credential_access", "attack.t1110.001"]},
  {"rule": "SSH brute force", "level": "high", "tags": ["attack.t1110.001"]},
  {"rule": "Encoded PowerShell", "level": "medium", "tags": ["attack.execution", "attack.t1059.001"]},
  {"rule": "Odd thing", "level": "low", "tags": ["attack.t1999"]},
  {"rule": "No tags", "level": "low"},
]
let m = attack.map(alerts)
say(m.techniques.map(fn(t) { return f"{t.id}x{t.count}" }))
say(m.tactics.map(fn(t) { return f"{t.tactic}:{t.count}" }))
say(m.unmapped, m.unknown, m.techniques[0].rules)
say(attack.tags({"tags": ["attack.T1110", "attack.credential_access"]}), attack.tag("T1110.001"))
'''
    assert run(src) == ['["T1110.001x2", "T1059.001x1"]', '["execution:1", "credential-access:2"]',
                        '1 ["T1999"] ["SSH brute force"]', '["T1110"] attack.t1110.001']


def test_info_and_techniques():
    out = run('let i = attack.info()\nsay(i.snapshot, i.tactics, i.techniques + i.sub_techniques == len(attack.techniques(true)))')
    assert out == ["approximate, curated 14 true"]


def test_load_official_stix_bundle_and_save(tmp_path, monkeypatch):
    bundle = {"type": "bundle", "objects": [
        {"type": "x-mitre-collection", "x_mitre_version": "15.1"},
        {"type": "x-mitre-tactic", "name": "Credential Access", "x_mitre_shortname": "credential-access",
         "external_references": [{"source_name": "mitre-attack", "external_id": "TA0006"}],
         "description": "The adversary is trying to steal account names and passwords."},
        {"type": "attack-pattern", "name": "Brute Force", "x_mitre_platforms": ["Linux"],
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "credential-access"}],
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1110"}],
         "description": "Adversaries may use brute force techniques. More text (Citation: Someone)."},
        {"type": "attack-pattern", "name": "Old", "revoked": True,
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1000"}]},
    ]}
    (tmp_path / "enterprise-attack.json").write_text(json.dumps(bundle))
    monkeypatch.chdir(tmp_path)
    out = run('say(attack.load("enterprise-attack.json"))\nlet t = attack.technique("T1110")\nsay(t.description)\n'
              'say(attack.info().snapshot)\nattack.save("small.json")\nsay(attack.load("small.json"))')
    assert out == ["1", "Adversaries may use brute force techniques.", "official ATT&CK 15.1", "1"]
    # another program still sees the bundled table
    assert run('say(attack.info().snapshot)') == ["approximate, curated"]


def test_load_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.json").write_text('{"type": "bundle", "objects": []}')
    assert "has no ATT&CK techniques" in str(tb_error('import attack\nattack.load("x.json")'))
    assert "I couldn't find 'nope.json'" in str(tb_error('import attack\nattack.load("nope.json")'))
