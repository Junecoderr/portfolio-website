"""Render the Apps gallery for examples.html from apps/*/main.tb + README.md.

build_site.py puts the result where examples.html has its
"APPS GALLERY" marker. Screenshots come from
site/assets/img/apps/<name>.png when there is one.
"""

from __future__ import annotations

import html
import re
import struct
from pathlib import Path

TITLES = {
    "todo": "To-do list", "care": "Care check-in", "notes": "Notes", "calculator": "Calculator",
    "hash-checker": "Hash checker", "log-triage-gui": "Log triage (desktop)", "ioc-workbench": "IOC workbench",
    "alerts-dashboard-gui": "Alerts dashboard (desktop)", "network-monitor": "Network monitor",
    "file-inspector-gui": "File inspector (desktop)",
    "log-triage": "Log triage", "ioc-extractor": "IOC extractor", "brute-force-hunter": "Brute-force hunter",
    "sigma-runner": "Sigma runner", "sigma-studio": "Sigma studio", "pcap-triage": "Packet capture triage",
    "evtx-triage": "Windows event log triage", "file-inspector": "File inspector", "ioc-feed": "IOC feed",
    "siem-forwarder": "SIEM forwarder",
    "ip-reputation": "IP reputation", "port-check": "Port check", "uptime-monitor": "Uptime monitor",
    "dns-check": "DNS check", "cert-check": "Certificate check", "parallel-scan": "Parallel port scan",
    "dashboard": "Alerts dashboard (web)",
}
ORDER = ["todo", "care", "notes", "calculator", "hash-checker", "log-triage-gui", "ioc-workbench",
         "alerts-dashboard-gui", "network-monitor", "file-inspector-gui",
         "log-triage", "ioc-extractor", "brute-force-hunter", "sigma-runner", "sigma-studio", "pcap-triage",
         "evtx-triage", "file-inspector", "ioc-feed", "siem-forwarder",
         "ip-reputation", "port-check", "uptime-monitor", "dns-check", "cert-check", "parallel-scan",
         "dashboard"]
KINDS = [("gui", "Desktop apps", "Real windows with buttons, tables, tabs and charts, built with the gui library."),
         ("soc", "Security (SOC) tools", "Command-line tools for the jobs a security analyst does every day: logs, "
          "packet captures, Windows event logs, executables, Sigma rules, threat feeds and SIEMs."),
         ("network", "Network and intel", "Check hosts you own, watch websites, look up DNS and certificates, and ask "
          "threat-intelligence services about indicators."),
         ("web", "Web apps", "A small web application served by TB itself with the server library.")]


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def inline_md(text: str) -> str:
    out = esc(text)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def readme_parts(text: str) -> tuple[str, str | None]:
    """(first paragraph, first fenced code block or None)"""
    lines = text.splitlines()
    para, fence, in_fence, block = [], None, False, []
    started = False
    for line in lines:
        if line.startswith("```"):
            if in_fence:
                if fence is None:
                    fence = "\n".join(block)
                in_fence, block = False, []
            else:
                in_fence = True
                if para:
                    started = True
            continue
        if in_fence:
            block.append(line)
            continue
        if line.startswith("#"):
            if para:
                started = True
            continue
        if not started:
            if line.strip():
                para.append(line.strip())
            elif para:
                started = True
    return " ".join(para), fence


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    return 640, 440


def app_kind(folder: Path) -> str:
    src = "\n".join(p.read_text(encoding="utf-8") for p in folder.glob("*.tb"))
    if re.search(r"^\s*import\s+gui\b", src, re.M):
        return "gui"
    if re.search(r"^\s*import\s+server\b", src, re.M):
        return "web"
    if re.search(r"^\s*import\s+(net|http|intel|dns|tls|parallel)\b", src, re.M):
        return "network"
    return "soc"


def libraries_used(folder: Path) -> list[str]:
    src = "\n".join(p.read_text(encoding="utf-8") for p in folder.glob("*.tb"))
    found = []
    for name in re.findall(r"^\s*import\s+(\w+)", src, re.M):
        if name not in found:
            found.append(name)
    return found


def render(apps_dir: Path, out: Path) -> str:
    folders = [f for f in apps_dir.iterdir() if f.is_dir() and (f / "main.tb").exists()]
    folders.sort(key=lambda f: (ORDER.index(f.name) if f.name in ORDER else len(ORDER), f.name))
    by_kind: dict[str, list[str]] = {k: [] for k, *_ in KINDS}
    for folder in folders:
        name = folder.name
        title = TITLES.get(name, name.replace("-", " ").capitalize())
        readme = (folder / "README.md").read_text(encoding="utf-8") if (folder / "README.md").exists() else ""
        about, sample = readme_parts(readme)
        main = (folder / "main.tb").read_text(encoding="utf-8").rstrip("\n")
        kind = app_kind(folder)
        shot = out / "assets" / "img" / "apps" / f"{name}.png"
        if shot.exists():
            w, h = png_size(shot)
            figure = (f'<figure class="app-shot"><img src="assets/img/apps/{name}.png" alt="Screenshot of the {esc(title)} app" '
                      f'loading="lazy" width="{w}" height="{h}"></figure>')
        elif sample:
            figure = f'<figure class="app-shot app-sample"><figcaption>Example run</figcaption><pre>{esc(sample)}</pre></figure>'
        else:
            figure = ""
        libs = "".join(f'<li><a href="lib/{l}.html"><code>{l}</code></a></li>' for l in libraries_used(folder))
        files = sorted(p.name for p in (out / "apps" / name).iterdir() if p.is_file()) if (out / "apps" / name).is_dir() else ["main.tb"]
        file_links = " ".join(f'<a href="apps/{name}/{f}" download>{esc(f)}</a>' for f in files)
        lines = main.count("\n") + 1
        by_kind[kind].append(f"""        <article class="app" id="app-{name}">
          <div class="app-info">
            <h4>{esc(title)} <code class="app-folder">apps/{name}</code></h4>
            <p>{inline_md(about)}</p>
            <ul class="ex-tags" aria-label="Libraries used">{libs}</ul>
            <p class="app-run">From your TB folder: <code>tb apps/{name}/main.tb</code></p>
            <p class="app-files">Download source: {file_links}</p>
          </div>
          {figure}
          <details class="app-source">
            <summary><code>main.tb</code>, {lines} lines</summary>
            <pre class="tb-code" id="app-src-{name}"><code>{esc(main)}</code></pre>
          </details>
        </article>""")
    sections = []
    for key, heading, desc in KINDS:
        if not by_kind[key]:
            continue
        sections.append(f"""      <section class="apps-group" id="apps-{key}" aria-labelledby="apps-{key}-title">
        <h3 class="apps-group-title" id="apps-{key}-title">{esc(heading)}</h3>
        <p>{esc(desc)}</p>
{chr(10).join(by_kind[key])}
      </section>""")
    return "\n".join(sections)
