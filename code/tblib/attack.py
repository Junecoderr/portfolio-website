"""attack: look up MITRE ATT&CK Enterprise techniques and map alerts to them."""

from __future__ import annotations

import json as _json
import os
import re

from tblib._api import Module, check_type, fail, fail_os

import tb as _tb

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "attack.json")
TECH_ID = re.compile(r"^t?(\d{4})(?:[._/](\d{3}))?$", re.IGNORECASE)
TAG_TECH = re.compile(r"^attack\.(t\d{4}(?:\.\d{3})?)$", re.IGNORECASE)
TAG_TACTIC = re.compile(r"^attack\.([a-z][a-z_\-]+)$", re.IGNORECASE)
CITATION = re.compile(r"\s*\(Citation:[^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")

_DEFAULT = None  # parsed bundled table, shared by every program (read-only)


class AttackError(Exception):
    pass


# =============================================================================
# The table
# =============================================================================

class Table:
    """Techniques and tactics with the indexes the lookups need."""

    def __init__(self, data: dict, source: str):
        if not isinstance(data, dict) or not isinstance(data.get("techniques"), list):
            raise AttackError("it has no \"techniques\" list")
        self.source = source
        self.snapshot = str(data.get("snapshot") or "unknown")
        self.version = str(data["version"]) if data.get("version") else None
        self.modified = str(data["modified"]) if data.get("modified") else None
        self.copyright = str(data["copyright"]) if data.get("copyright") else None
        self.mitigations, self.groups = ({str(x["id"]): x for x in data.get(key) or []
                                          if isinstance(x, dict) and x.get("id") and x.get("name")}
                                         for key in ("mitigations", "groups"))
        self.tactics = []
        for t in data.get("tactics") or []:
            if isinstance(t, dict) and t.get("shortname"):
                self.tactics.append({"id": str(t.get("id") or ""), "shortname": str(t["shortname"]),
                                     "name": str(t.get("name") or t["shortname"]),
                                     "description": str(t.get("description") or "")})
        self.techniques = {}
        for t in data["techniques"]:
            if not isinstance(t, dict) or not t.get("id") or not t.get("name"):
                continue
            tid = str(t["id"]).upper()
            item = {"id": tid, "name": str(t["name"]), "tactics": [str(x) for x in t.get("tactics") or []],
                    "platforms": [str(x) for x in t.get("platforms") or []],
                    "description": str(t.get("description") or "")}
            for key in ("mitigations", "groups"):
                if isinstance(t.get(key), list):
                    item[key] = [str(x) for x in t[key]]
            if "." in tid:
                item["parent"] = tid.split(".")[0]
            self.techniques[tid] = item
        if not self.tactics:  # derive from the techniques when the file has no tactic list
            seen = []
            for t in self.techniques.values():
                for s in t["tactics"]:
                    if s not in seen:
                        seen.append(s)
            self.tactics = [{"id": "", "shortname": s, "name": s.replace("-", " ").title(), "description": ""} for s in seen]
        self.tactic_index = {}
        for t in self.tactics:
            for key in (t["shortname"], t["id"], t["name"]):
                if key:
                    self.tactic_index[norm_tactic(key)] = t
        # ATT&CK v19 split Defense Evasion (TA0005) into Stealth and Defense Impairment; Sigma rules still
        # tag attack.defense_evasion, so that name keeps pointing at TA0005.
        if "defense-evasion" not in self.tactic_index and "ta0005" in self.tactic_index:
            self.tactic_index["defense-evasion"] = self.tactic_index["ta0005"]

    def lookup(self, text):
        tid = technique_id(text)
        return self.techniques.get(tid) if tid else None

    def full(self, item) -> dict:
        """A technique map as TB sees it (with url, sub-techniques and the parent's name)."""
        out = dict(item)
        out["tactics"] = list(item["tactics"])
        out["platforms"] = list(item["platforms"])
        out["url"] = url_of(item["id"])
        for key, known in (("mitigations", self.mitigations), ("groups", self.groups)):
            out[key] = [{"id": x, "name": known[x]["name"]} for x in item.get(key, []) if x in known]
        if "parent" in item:
            parent = self.techniques.get(item["parent"])
            out["parent_name"] = parent["name"] if parent else None
            out["full_name"] = f"{parent['name']}: {item['name']}" if parent else item["name"]
        else:
            out["sub_techniques"] = sorted(k for k in self.techniques if k.startswith(item["id"] + "."))
            out["full_name"] = item["name"]
        return out


def norm_tactic(text: str) -> str:
    return re.sub(r"[\s_]+", "-", str(text).strip().lower())


def technique_id(text):
    """'t1110.001', 'T1110/001', 'attack.t1110', '1110' -> 'T1110.001' / 'T1110'; None if not an id."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.lower().startswith("attack."):
        s = s[7:]
    m = TECH_ID.match(s)
    if not m:
        return None
    return f"T{m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")


def url_of(tid: str) -> str:
    return "https://attack.mitre.org/techniques/" + tid.replace(".", "/") + "/"


def default_table() -> Table:
    global _DEFAULT
    if _DEFAULT is None:
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                _DEFAULT = Table(_json.load(f), "bundled")
        except (OSError, ValueError, AttackError):
            _DEFAULT = Table({"snapshot": "missing", "tactics": [], "techniques": []}, "bundled")
    return _DEFAULT


def first_sentence(text: str) -> str:
    text = LINK.sub(r"\1", CITATION.sub("", text or "")).replace("<code>", "").replace("</code>", "").strip()
    text = " ".join(text.split())
    m = re.search(r"(?<=[a-z0-9)\]])\.\s", text)
    return text[: m.start() + 1] if m else text[:300]


def mitre_id(o):
    ext = next((r for r in o.get("external_references") or []
                if isinstance(r, dict) and r.get("source_name") == "mitre-attack" and r.get("external_id")), None)
    return ext["external_id"] if ext else None


def from_stix(bundle: dict) -> dict:
    """The official enterprise-attack.json (a STIX 2 bundle) -> TB's compact table, with the ATT&CK
    version, the bundle's modified date, mitigations and groups (and which techniques each touches)."""
    objects = bundle.get("objects") if isinstance(bundle, dict) else None
    if not isinstance(objects, list):
        raise AttackError("it isn't a STIX bundle (no \"objects\" list)")
    tactics, techniques, mitigations, groups = [], [], [], []
    version = modified = copyright_ = None
    by_stix, relations = {}, []
    for o in objects:
        if not isinstance(o, dict):
            continue
        kind = o.get("type")
        if kind == "relationship":
            if not o.get("revoked") and not o.get("x_mitre_deprecated"):
                relations.append(o)
            continue
        if kind == "marking-definition":
            definition = o.get("definition") if isinstance(o.get("definition"), dict) else {}
            statement = definition.get("statement")
            if isinstance(statement, str) and "MITRE" in statement:
                copyright_ = statement
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        ext_id = mitre_id(o)
        if kind == "x-mitre-collection":
            version = o.get("x_mitre_version") or version
            modified = o.get("modified") or modified
        elif kind == "x-mitre-tactic" and ext_id:
            tactics.append({"id": ext_id, "shortname": o.get("x_mitre_shortname") or norm_tactic(o.get("name", "")),
                            "name": o.get("name", ""), "description": first_sentence(o.get("description", ""))})
        elif kind == "attack-pattern" and ext_id:
            item = {
                "id": ext_id, "name": o.get("name", ""),
                "tactics": [p.get("phase_name") for p in o.get("kill_chain_phases") or []
                            if isinstance(p, dict) and p.get("kill_chain_name") == "mitre-attack"],
                "platforms": list(o.get("x_mitre_platforms") or []),
                "description": first_sentence(o.get("description", "")),
            }
            techniques.append(item)
            by_stix[o.get("id")] = item
        elif kind == "course-of-action" and ext_id and ext_id.startswith("M"):
            item = {"id": ext_id, "name": o.get("name", ""), "description": first_sentence(o.get("description", ""))}
            mitigations.append(item)
            by_stix[o.get("id")] = item
        elif kind == "intrusion-set" and ext_id:
            item = {"id": ext_id, "name": o.get("name", ""),
                    "aliases": [a for a in o.get("aliases") or [] if isinstance(a, str) and a != o.get("name")]}
            groups.append(item)
            by_stix[o.get("id")] = item
    if not techniques:
        raise AttackError("it has no ATT&CK techniques (attack-pattern objects)")
    tech_ids = {id(t) for t in techniques}
    for r in relations:
        src, dst = by_stix.get(r.get("source_ref")), by_stix.get(r.get("target_ref"))
        if src is None or dst is None or id(dst) not in tech_ids:
            continue
        key = {"mitigates": "mitigations", "uses": "groups"}.get(r.get("relationship_type"))
        if key and src["id"][0] == ("M" if key == "mitigations" else "G"):
            dst.setdefault(key, [])
            if src["id"] not in dst[key]:
                dst[key].append(src["id"])
    for t in techniques:
        for key in ("mitigations", "groups"):
            if key in t:
                t[key].sort()
    order = {"TA0043": 0, "TA0042": 1, "TA0001": 2, "TA0002": 3, "TA0003": 4, "TA0004": 5, "TA0005": 6, "TA0112": 6.5,
             "TA0006": 7, "TA0007": 8, "TA0008": 9, "TA0009": 10, "TA0011": 11, "TA0010": 12, "TA0040": 13}
    tactics.sort(key=lambda t: (order.get(t["id"], 99), t["id"]))
    for items in (techniques, mitigations, groups):
        items.sort(key=lambda t: t["id"])
    out = {"snapshot": f"official ATT&CK v{version}" if version else "official ATT&CK"}
    if version:
        out["version"] = str(version)
    if modified:
        out["modified"] = str(modified)
    if copyright_:
        out["copyright"] = copyright_
    out.update({"tactics": tactics, "techniques": techniques, "mitigations": mitigations, "groups": groups})
    return out


# =============================================================================
# Mapping alerts
# =============================================================================

def tags_of(item) -> list:
    """Tags from an alert, a rule, or a list of tags."""
    if isinstance(item, list):
        return [t for t in item if isinstance(t, str)]
    if isinstance(item, str):
        return [item]
    if not isinstance(item, dict):
        return []
    tags = item.get("tags")
    out = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    rule = item.get("rule")
    if isinstance(rule, dict) and isinstance(rule.get("tags"), list):
        out.extend(t for t in rule["tags"] if isinstance(t, str))
    for key in ("technique", "techniques", "attack"):
        v = item.get(key)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
    return out


def rule_name(item) -> str:
    if isinstance(item, dict):
        r = item.get("rule")
        if isinstance(r, dict):
            r = r.get("title") or r.get("name")
        for v in (r, item.get("title"), item.get("name")):
            if isinstance(v, str) and v:
                return v
    return "(unnamed)"


def map_alerts(table: Table, alerts: list) -> dict:
    techniques, tactic_counts, unmapped, unknown = {}, {}, 0, []
    for alert in alerts:
        tags = tags_of(alert)
        ids, tactics = [], []
        for tag in tags:
            tid = technique_id(tag) if (tag.lower().startswith("attack.t") or TECH_ID.match(tag.strip())) else None
            if tid:
                if tid not in ids:
                    ids.append(tid)
                continue
            m = TAG_TACTIC.match(tag.strip())
            if m:
                found = table.tactic_index.get(norm_tactic(m.group(1)))
                if found and found["shortname"] not in tactics:
                    tactics.append(found["shortname"])
        if not ids and not tactics:
            unmapped += 1
            continue
        for tid in ids:
            tech = table.techniques.get(tid)
            if tech is None:
                if tid not in unknown:
                    unknown.append(tid)
                continue
            entry = techniques.get(tid)
            if entry is None:
                entry = techniques[tid] = {"id": tid, "name": table.full(tech)["full_name"], "tactics": list(tech["tactics"]),
                                           "url": url_of(tid), "count": 0, "rules": []}
            entry["count"] += 1
            name = rule_name(alert)
            if name not in entry["rules"]:
                entry["rules"].append(name)
            for s in tech["tactics"]:
                if s not in tactics:
                    tactics.append(s)
        for s in tactics:
            tactic_counts[s] = tactic_counts.get(s, 0) + 1
    order = [t["shortname"] for t in table.tactics]
    tactic_list = []
    for s in sorted(tactic_counts, key=lambda s: order.index(s) if s in order else 99):
        info = table.tactic_index.get(s)
        tactic_list.append({"tactic": s, "name": info["name"] if info else s, "id": info["id"] if info else "",
                            "count": tactic_counts[s],
                            "techniques": sorted(k for k, v in techniques.items() if s in v["tactics"])})
    tech_list = sorted(techniques.values(), key=lambda e: (-e["count"], e["id"]))
    return {"techniques": tech_list, "tactics": tactic_list, "unmapped": unmapped, "unknown": unknown}


# =============================================================================
# The library
# =============================================================================

def load(api):
    m = Module(
        "attack",
        "MITRE ATT&CK Enterprise at your fingertips: look up a technique by id (attack.technique(\"T1110\")), search "
        "by words, list a tactic's techniques, and map detect alerts to techniques through their Sigma tags "
        "(attack.t1110.001). TB bundles the official Enterprise dataset from MITRE, made compact: ids, names, "
        "tactics, platforms, the first sentence of each description, mitigations and groups. attack.info() tells "
        "which ATT&CK version it is; attack.load(path) switches to another enterprise-attack.json. "
        "ATT&CK is (c) The MITRE Corporation, used under its terms of use; attack.info().copyright has the notice.",
    )
    state = {"table": default_table()}

    def table() -> Table:
        return state["table"]

    def need_id(line, text, fn):
        check_type(line, text, "text", fn, "id")
        tid = technique_id(text)
        if tid is None:
            fail(line, f"{fn}() needs a technique id like \"T1110\" or \"T1110.001\", not '{text}'.")
        return tid

    @m.fn("technique", 1, doc=(
        "technique(id) -> map\n"
        "One technique or sub-technique: {\"id\", \"name\", \"full_name\", \"tactics\", \"platforms\", \"description\", "
        "\"url\", \"sub_techniques\" (or \"parent\" and \"parent_name\"), \"mitigations\" and \"groups\" "
        "(lists of {\"id\", \"name\"})}. id can be \"T1110\", \"t1110.001\" or a "
        "Sigma tag like \"attack.t1110.001\". Gives nil when the id isn't in the table."))
    def technique(line, text):
        tid = need_id(line, text, "attack.technique")
        item = table().techniques.get(tid)
        return table().full(item) if item else None

    @m.fn("search", 1, 2, doc=(
        "search(words, limit?) -> list\n"
        "Techniques whose id, name or description contain every word (case doesn't matter), best matches first: "
        "name matches before description matches. attack.search(\"brute\") finds Brute Force and its sub-techniques."))
    def search(line, words, limit=None):
        check_type(line, words, "text", "attack.search", "words")
        if limit is not None:
            limit = check_type(line, limit, "whole", "attack.search", "limit")
        terms = [w for w in words.lower().split() if w]
        if not terms:
            return []
        scored = []
        for item in table().techniques.values():
            name = item["name"].lower()
            parent = table().techniques.get(item.get("parent", ""), {}).get("name", "").lower()
            hay = " ".join([item["id"].lower(), name, parent, item["description"].lower()])
            if not all(t in hay for t in terms):
                continue
            score = 0
            for t in terms:
                if t == item["id"].lower():
                    score += 100
                if t in name:
                    score += 10
                if t in parent:
                    score += 5
            scored.append((-score, item["id"], item))
        scored.sort(key=lambda s: (s[0], s[1]))
        found = [table().full(s[2]) for s in scored]
        return found[:limit] if limit is not None else found

    @m.fn("tactic", 1, 2, doc=(
        "tactic(name, platform?) -> list\n"
        "The techniques (without sub-techniques) under a tactic.\n"
        "\n"
        "name can be:\n"
        "- the short name, like \"credential-access\".\n"
        "- the Sigma tag spelling, like \"credential_access\".\n"
        "- the display name, like \"Credential Access\".\n"
        "- the id, like \"TA0006\".\n"
        "\n"
        "platform (\"Windows\", \"Linux\", \"macOS\", \"IaaS\"...) keeps only the techniques for it.\n"
        "\n"
        "Example:\n"
        "    let found = attack.tactic(\"credential-access\", \"Linux\")\n"
        "    say(found[0].name)"))
    def tactic(line, name, platform=None):
        check_type(line, name, "text", "attack.tactic", "name")
        key = norm_tactic(name[7:] if name.lower().startswith("attack.") else name)
        info = table().tactic_index.get(key)
        if info is None:
            names = ", ".join(t["shortname"] for t in table().tactics)
            fail(line, f"attack.tactic() doesn't know the tactic '{name}'. Tactics: {names}.")
        if platform is not None:
            check_type(line, platform, "text", "attack.tactic", "platform")
        out = []
        for item in table().techniques.values():
            if "parent" in item or info["shortname"] not in item["tactics"]:
                continue
            if platform is not None and platform.lower() not in [p.lower() for p in item["platforms"]]:
                continue
            out.append(table().full(item))
        return sorted(out, key=lambda t: t["id"])

    @m.fn("tactics", 0, doc=(
        "tactics() -> list\n"
        "The Enterprise tactics in kill-chain order (reconnaissance first, impact last): "
        "[{\"id\": \"TA0043\", \"shortname\": \"reconnaissance\", \"name\", \"description\"}, ...]."))
    def tactics(line):
        return [dict(t) for t in table().tactics]

    @m.fn("techniques", 0, 1, doc=(
        "techniques(all?) -> list\n"
        "Every top-level technique in the table, by id. attack.techniques(true) includes sub-techniques too."))
    def techniques(line, include_subs=False):
        check_type(line, include_subs, "bool", "attack.techniques", "all")
        return [table().full(t) for t in sorted(table().techniques.values(), key=lambda t: t["id"])
                if include_subs or "parent" not in t]

    @m.fn("map", 1, doc=(
        "map(alerts) -> map\n"
        "Which ATT&CK techniques and tactics a list of alerts (from detect.run()) or rules points at. "
        "It reads their tags, like attack.t1110.001 and attack.credential_access.\n"
        "\n"
        "Returns a map:\n"
        "- \"techniques\": a list of {\"id\", \"name\", \"tactics\", \"url\", \"count\", \"rules\"},\n"
        "  most alerts first.\n"
        "- \"tactics\": a list of {\"tactic\", \"name\", \"id\", \"count\", \"techniques\"},\n"
        "  in kill-chain order.\n"
        "- \"unmapped\": alerts without ATT&CK tags.\n"
        "- \"unknown\": technique ids the table doesn't have."))
    def map_fn(line, alerts):
        check_type(line, alerts, "list", "attack.map", "alerts")
        return map_alerts(table(), alerts)

    @m.fn("tags", 1, doc=(
        "tags(alert) -> list\n"
        "The technique ids an alert, rule or list of tags points at: [\"T1110.001\"]. Tactic tags are left out."))
    def tags(line, item):
        out = []
        for tag in tags_of(item):
            if tag.lower().startswith("attack.t") or TECH_ID.match(tag.strip()):
                tid = technique_id(tag)
                if tid and tid not in out:
                    out.append(tid)
        return out

    @m.fn("tag", 1, doc=(
        "tag(id) -> text\n"
        "The Sigma tag for a technique id: attack.tag(\"T1110.001\") is \"attack.t1110.001\"."))
    def tag(line, text):
        return "attack." + need_id(line, text, "attack.tag").lower()

    @m.fn("url", 1, doc=(
        "url(id) -> text\n"
        "The technique's page on attack.mitre.org: \"https://attack.mitre.org/techniques/T1110/001/\"."))
    def url(line, text):
        return url_of(need_id(line, text, "attack.url"))

    @m.fn("load", 1, doc=(
        "load(path) -> number\n"
        "Use a different ATT&CK table for the rest of the program: the official enterprise-attack.json (a STIX "
        "bundle from MITRE's attack-stix-data repository) or a file written by attack.save(). Revoked and "
        "deprecated techniques are skipped. Gives back how many techniques were loaded."))
    def load_fn(line, path):
        check_type(line, path, "text", "attack.load", "path")
        full = path
        try:
            with open(full, encoding="utf-8") as f:
                data = _json.load(f)
        except OSError as err:
            fail_os(line, err, path, "read")
        except ValueError as err:
            fail(line, f"attack.load() couldn't read '{path}' as JSON: {err}.")
        try:
            if isinstance(data, dict) and data.get("type") == "bundle":
                data = from_stix(data)
            state["table"] = Table(data, path)
        except AttackError as err:
            fail(line, f"attack.load() can't use '{path}': {err}.")
        return len(state["table"].techniques)

    @m.fn("save", 1, doc=(
        "save(path) -> nil\n"
        "Write the table in use as TB's compact JSON format (handy after attack.load() of the big official file)."))
    def save(line, path):
        check_type(line, path, "text", "attack.save", "path")
        t = table()
        data = {"snapshot": t.snapshot, "version": t.version, "modified": t.modified, "copyright": t.copyright,
                "tactics": t.tactics,
                "techniques": [{k: v for k, v in item.items() if k != "parent"} for item in
                               sorted(t.techniques.values(), key=lambda x: x["id"])],
                "mitigations": list(t.mitigations.values()), "groups": list(t.groups.values())}
        full = path
        try:
            with open(full, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=1)
                f.write("\n")
        except OSError as err:
            fail_os(line, err, path, "write")
        return None

    @m.fn("info", 0, doc=(
        "info() -> map\n"
        "About the table in use.\n"
        "\n"
        "Fields:\n"
        "- version: the ATT&CK release, like \"18.0\" (nil if the file doesn't say).\n"
        "- modified: when MITRE last changed that release.\n"
        "- snapshot: a label, like \"official ATT&CK v18.0\".\n"
        "- source: \"bundled\" or the path given to attack.load().\n"
        "- techniques, sub_techniques, tactics, mitigations, groups: how many.\n"
        "- copyright: MITRE's notice for the data."))
    def info(line):
        t = table()
        subs = sum(1 for x in t.techniques.values() if "parent" in x)
        return {"version": t.version, "modified": t.modified, "snapshot": t.snapshot, "source": t.source,
                "techniques": len(t.techniques) - subs, "sub_techniques": subs, "tactics": len(t.tactics),
                "mitigations": len(t.mitigations), "groups": len(t.groups), "copyright": t.copyright}

    return m
