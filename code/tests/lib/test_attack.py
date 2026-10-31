"""attack library: the bundled official ATT&CK table, lookups, alert mapping, and loading the official STIX file."""

import json

import pytest

from conftest import ROOT, run_tb, tb_error
from tblib import attack


def run(source):
    return run_tb("import attack\n" + source)


def test_bundled_table_is_official_and_well_formed():
    path = ROOT / "tblib" / "data" / "attack.json"
    assert path.stat().st_size < 3_000_000
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["snapshot"] == f"official ATT&CK v{data['version']}"
    assert float(data["version"]) >= 15 and data["modified"][:2] == "20"
    assert "The MITRE Corporation" in data["copyright"] and "royalty-free license" in data["notice"]
    ids = [t["id"] for t in data["techniques"]]
    assert len(ids) == len(set(ids)) == data["count"] >= 550
    shortnames = {t["shortname"] for t in data["tactics"]}
    assert len(shortnames) >= 14
    mitigations, groups = {m["id"] for m in data["mitigations"]}, {g["id"] for g in data["groups"]}
    assert len(mitigations) >= 40 and len(groups) >= 100
    for t in data["techniques"]:
        assert t["name"] and t["description"] and t["platforms"], t["id"]
        assert set(t["tactics"]) <= shortnames, t["id"]
        assert set(t.get("mitigations", [])) <= mitigations and set(t.get("groups", [])) <= groups, t["id"]
        if "." in t["id"]:
            assert t["id"].split(".")[0] in ids


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
    assert run('let t = attack.tactics()\nsay(t[0].shortname, t[-1].shortname)') == ["reconnaissance impact"]
    # ATT&CK v19 split Defense Evasion; Sigma's attack.defense_evasion means both of its successors.
    assert run('say(attack.map([{"tags": ["attack.defense_evasion"]}]).tactics.map(fn(t) { return t.id }))') == \
        ['["TA0005", "TA0112"]']
    out = run('let ids = attack.tactic("defense_evasion").map(fn(t) { return t.id })\n'
              'say(ids.contains("T1027"), ids.contains("T1685"), len(ids) == len(attack.tactic("stealth")) + '
              'len(attack.tactic("defense-impairment")))')
    assert out == ["true true true"]   # T1027 Obfuscation (stealth), T1685 Disable or Modify Tools (impairment)


def test_defense_evasion_reaches_impair_defenses(tmp_path):
    """T1562 Impair Defenses (TA0112 in a table that still has it) is found under the legacy name."""
    import json
    table = {"snapshot": "test", "tactics": [
        {"id": "TA0005", "shortname": "stealth", "name": "Stealth"},
        {"id": "TA0112", "shortname": "defense-impairment", "name": "Defense Impairment"}],
        "techniques": [{"id": "T1562", "name": "Impair Defenses", "tactics": ["defense-impairment"]},
                       {"id": "T1027", "name": "Obfuscated Files or Information", "tactics": ["stealth"]}]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(table))
    out = run(f'attack.load("{path.as_posix()}")\n'
              'say(attack.tactic("defense-evasion").map(fn(t) { return t.id }))\n'
              'let m = attack.map([{"tags": ["attack.defense_evasion", "attack.t1562"]}])\n'
              'say(m.tactics.map(fn(t) { return t.tactic + " " + str(t.techniques) }))')
    assert out == ['["T1027", "T1562"]', '["stealth []", "defense-impairment [\\"T1562\\"]"]']


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


def test_info_reports_the_official_version():
    data = json.loads((ROOT / "tblib" / "data" / "attack.json").read_text(encoding="utf-8"))
    out = run('let i = attack.info()\nsay(i.version, i.modified, i.snapshot, i.source, i.tactics >= 14)\n'
              'say(i.techniques + i.sub_techniques == len(attack.techniques(true)), i.mitigations > 0, i.groups > 0)\n'
              'say(i.copyright)')
    assert out == [f"{data['version']} {data['modified']} official ATT&CK v{data['version']} bundled true",
                   "true true true", data["copyright"]]


def test_technique_has_mitigations_and_groups():
    out = run('let t = attack.technique("T1110")\nsay(t.mitigations.map(fn(m) { return m.id }).contains("M1032"))\n'
              'say(t.groups.map(fn(g) { return g.id }).contains("G0007"), t.groups[0].name != "")')
    assert out == ["true", "true true"]


def test_load_official_stix_bundle_and_save(tmp_path, monkeypatch):
    bundle = {"type": "bundle", "objects": [
        {"type": "x-mitre-collection", "x_mitre_version": "15.1", "modified": "2024-05-02T14:00:00.188Z"},
        {"type": "marking-definition", "definition": {"statement": "Copyright 2015-2024, The MITRE Corporation."}},
        {"type": "course-of-action", "id": "course-of-action--1", "name": "Multi-factor Authentication",
         "external_references": [{"source_name": "mitre-attack", "external_id": "M1032"}]},
        {"type": "intrusion-set", "id": "intrusion-set--1", "name": "APT28", "aliases": ["APT28", "Fancy Bear"],
         "external_references": [{"source_name": "mitre-attack", "external_id": "G0007"}]},
        {"type": "relationship", "relationship_type": "mitigates", "source_ref": "course-of-action--1",
         "target_ref": "attack-pattern--1"},
        {"type": "relationship", "relationship_type": "uses", "source_ref": "intrusion-set--1",
         "target_ref": "attack-pattern--1"},
        {"type": "x-mitre-tactic", "name": "Credential Access", "x_mitre_shortname": "credential-access",
         "external_references": [{"source_name": "mitre-attack", "external_id": "TA0006"}],
         "description": "The adversary is trying to steal account names and passwords."},
        {"type": "attack-pattern", "id": "attack-pattern--1", "name": "Brute Force", "x_mitre_platforms": ["Linux"],
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "credential-access"}],
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1110"}],
         "description": "Adversaries may use brute force techniques. More text (Citation: Someone)."},
        {"type": "attack-pattern", "name": "Old", "revoked": True,
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1000"}]},
    ]}
    (tmp_path / "enterprise-attack.json").write_text(json.dumps(bundle))
    monkeypatch.chdir(tmp_path)
    out = run('say(attack.load("enterprise-attack.json"))\nlet t = attack.technique("T1110")\nsay(t.description)\n'
              'say(attack.info().snapshot, attack.info().modified)\nattack.save("small.json")\nsay(attack.load("small.json"))\n'
              'let t2 = attack.technique("T1110")\nsay(t2.mitigations, t2.groups, attack.info().version)')
    assert out == ["1", "Adversaries may use brute force techniques.", "official ATT&CK v15.1 2024-05-02T14:00:00.188Z",
                   "1", '[{"id": "M1032", "name": "Multi-factor Authentication"}] [{"id": "G0007", "name": "APT28"}] 15.1']
    # another program still sees the bundled table
    assert run('say(attack.info().source)') == ["bundled"]


def test_update_script_builds_the_data_file(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_attack", ROOT / "scripts" / "update_attack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bundle = {"type": "bundle", "objects": [
        {"type": "x-mitre-collection", "x_mitre_version": "16.0", "modified": "2024-10-31T00:00:00Z"},
        {"type": "attack-pattern", "name": "Brute Force", "x_mitre_platforms": ["Linux"], "description": "One. Two.",
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1110"}]}]}
    data = json.loads(mod.dumps(mod.build(bundle)))
    assert (data["version"], data["modified"], data["count"]) == ("16.0", "2024-10-31T00:00:00Z", 1)
    assert "MITRE" in data["copyright"] and "royalty-free" in data["notice"]
    assert data["techniques"][0]["description"] == "One."


def test_load_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.json").write_text('{"type": "bundle", "objects": []}')
    assert "has no ATT&CK techniques" in str(tb_error('import attack\nattack.load("x.json")'))
    assert "I couldn't find 'nope.json'" in str(tb_error('import attack\nattack.load("nope.json")'))
