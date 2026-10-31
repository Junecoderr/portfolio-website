"""Rebuild tblib/data/attack.json from MITRE's official ATT&CK Enterprise STIX bundle.

Usage:
    uv run python scripts/update_attack.py                 # download the latest release
    uv run python scripts/update_attack.py --version 18.0  # a given release
    uv run python scripts/update_attack.py path/to/enterprise-attack.json

The bundle comes from MITRE's attack-stix-data repository (the official
source). tblib.attack.from_stix() keeps what TB needs: tactics, techniques and
sub-techniques (first sentence of each description), mitigations and groups,
plus the ATT&CK version and the bundle's modified date. The result is written
one object per line so updates diff well. Standard library only.

ATT&CK is (c) The MITRE Corporation; the terms of use ask every copy to carry
MITRE's copyright designation and license, so the file gets a "notice".
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tblib.attack import from_stix  # noqa: E402

BASE = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/"
OUT = ROOT / "tblib" / "data" / "attack.json"
TERMS = (
    "This file is derived from MITRE ATT&CK(R) Enterprise (the enterprise-attack STIX bundle in "
    "MITRE's attack-stix-data repository), shortened for TB: descriptions keep only their first sentence. "
    "Terms of use (https://attack.mitre.org/resources/legal-and-branding/terms-of-use/): The MITRE Corporation "
    "(MITRE) hereby grants you a non-exclusive, royalty-free license to use ATT&CK for research, development, and "
    "commercial purposes. Any copy you make for such purposes is authorized provided that you reproduce MITRE's "
    "copyright designation and this license in any such copy. ALL DOCUMENTS AND THE INFORMATION CONTAINED THEREIN "
    "ARE PROVIDED ON AN \"AS IS\" BASIS AND THE CONTRIBUTOR, THE ORGANIZATION HE/SHE REPRESENTS OR IS SPONSORED BY "
    "(IF ANY), THE MITRE CORPORATION, ITS BOARD OF TRUSTEES, OFFICERS, AGENTS, AND EMPLOYEES, DISCLAIM ALL "
    "WARRANTIES, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTY THAT THE USE OF THE INFORMATION "
    "THEREIN WILL NOT INFRINGE ANY RIGHTS OR ANY IMPLIED WARRANTIES OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR "
    "PURPOSE. MITRE ATT&CK and ATT&CK are registered trademarks of The MITRE Corporation."
)


def build(bundle: dict) -> dict:
    data = from_stix(bundle)
    out = {"snapshot": data.pop("snapshot"), "version": data.pop("version", None),
           "modified": data.pop("modified", None),
           "copyright": data.pop("copyright", "Copyright The MITRE Corporation."),
           "notice": TERMS, "source": "mitre-attack/attack-stix-data enterprise-attack.json"}
    out["count"] = len(data["techniques"])
    out.update(data)
    return out


def dumps(data: dict) -> str:
    """JSON with one list item per line: small, and readable in a diff."""
    lines = ["{"]
    keys = list(data)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        value = data[key]
        if isinstance(value, list):
            lines.append(f"  {json.dumps(key)}: [")
            lines += [f"    {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                      + ("," if j < len(value) - 1 else "") for j, v in enumerate(value)]
            lines.append(f"  ]{comma}")
        else:
            lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    return "\n".join(lines + ["}"]) + "\n"


def fetch(version: str | None) -> dict:
    url = BASE + (f"enterprise-attack-{version}.json" if version else "enterprise-attack.json")
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str]) -> int:
    version = None
    if argv[:1] == ["--version"] and len(argv) == 2:
        version, argv = argv[1], []
    if len(argv) > 1:
        print(__doc__)
        return 2
    if argv:
        bundle = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    else:
        bundle = fetch(version)
    data = build(bundle)
    text = dumps(data)
    json.loads(text)  # the hand-rolled layout must still be JSON
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT).as_posix()}: ATT&CK v{data['version']} ({data['modified']}), "
          f"{data['count']} techniques, {len(data['mitigations'])} mitigations, {len(data['groups'])} groups, "
          f"{len(text) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
