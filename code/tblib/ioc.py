"""ioc: find, check, defang and refang indicators of compromise."""

from __future__ import annotations

import ipaddress
import re

from tblib._api import Module, check_type, fail

# -- top-level domains --------------------------------------------------------

# Country-code TLDs (ISO 3166 two-letter codes plus a few extras like uk, eu, ac, su).
CC_TLDS = set("""
ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo bq br bs bt
bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg er es
et eu fi fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu
id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt
lu lv ly ma mc md me mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no
np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si
sk sl sm sn so sr ss st su sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us
uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw
""".split())

# Generic TLDs seen in real traffic and threat reports (not all ~1,500, but the
# ones that matter), plus reserved and internal names analysts meet in logs.
GENERIC_TLDS = set("""
com net org edu gov mil int info biz pro mobi asia tel travel jobs museum aero coop cat post
xxx arpa app dev page io ai co me tv cc ly gg sh to ws fm am la nu tk ml ga cf gq
xyz top online site club shop store tech cloud live life world today space website fun icu buzz
vip win bid loan work click link help support services service email digital network solutions
systems agency company center group global media news blog wiki host hosting domains download
software security secure exchange finance financial bank money pay cash credit capital fund
market markets trade trading business biz center consulting management marketing studio design
photo photos pics video stream tube games game casino bet poker art love sex porn adult dating
social chat team zone one run red blue pink black gold green best cool rocks ninja guru expert
academy school college university education training institute science health care clinic
dental doctor hospital pharmacy legal law lawyer attorney insurance realty estate properties
house home homes city london berlin paris tokyo nyc moscow africa amazon google microsoft apple
windows office azure aws gmail yahoo bing youtube facebook meta instagram whatsapp android
mozilla firefox chrome samsung sony intel cisco oracle ibm dell hp
onion local localhost internal lan corp intranet home example test invalid i2p bit
gdn men date party review trade racing download stream accountant science cricket faith
country kim pw rest su surf tokyo uno wtf zip mov
""".split())

KNOWN_TLDS = CC_TLDS | GENERIC_TLDS

# File extensions that look like TLDs. "report.pdf", "tb.py" or "jquery.min.js"
# are files, not domains. A name ending in one of these only counts as a domain
# when it has 3+ labels and the ending is also a real country code
# (www.example.pl), or when it sits inside a URL or an email address.
FILE_EXTENSIONS = set("""
exe dll sys drv scr cpl ocx com bat cmd ps1 psm1 psd1 vbs vbe js jse wsf wsh hta msi msp lnk
jar class apk app dmg pkg deb rpm iso img vhd vhdx
pdf doc docx docm xls xlsx xlsm ppt pptx pptm rtf odt ods odp txt log csv tsv json xml yml yaml
toml ini cfg conf config md rst html htm xhtml mht php asp aspx jsp cgi pl py pyc rb sh bash zsh
ps c h cpp hpp cs go rs java kt swift ts tsx jsx css scss vue tb lua r sql db sqlite mdb
zip rar 7z gz tgz bz2 xz tar cab
png jpg jpeg gif bmp svg ico webp tif tiff psd mp3 mp4 avi mov mkv wav flac ogg wmv m4a
tmp bak old swp dat bin out eml msg pst ost evtx pcap pcapng key pem crt cer der pfx p12
""".split())

# "com" is both a TLD and a DOS program extension: never treat it as a file.
FILE_EXTENSIONS -= {"com", "app"}

# -- patterns -------------------------------------------------------------------

OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
IPV4 = re.compile(rf"(?<![\w.])(?:{OCTET}\.){{3}}{OCTET}(?![\w]|\.[0-9])")
IPV4_FULL = re.compile(rf"(?:{OCTET}\.){{3}}{OCTET}")
IPV6_CANDIDATE = re.compile(
    r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3}|[0-9A-Fa-f]{1,4})?(?![\w:])"
)
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN = re.compile(rf"(?<![\w.-])((?:{LABEL}\.)+[a-z][a-z0-9-]*[a-z0-9])(?![\w-]|\.[a-z0-9])", re.IGNORECASE)
DOMAIN_FULL = re.compile(rf"(?:{LABEL}\.)+[a-z][a-z0-9-]*[a-z0-9]", re.IGNORECASE)
URL = re.compile(r"\b(?:https?|ftp|sftp|ftps|ws|wss)://[^\s<>\"'`{}|\\^]+", re.IGNORECASE)
EMAIL = re.compile(rf"(?<![\w.+-])[a-z0-9._%+-]+@((?:{LABEL}\.)+[a-z][a-z0-9-]*[a-z0-9])(?![\w-])", re.IGNORECASE)
HASHES = {
    "md5": re.compile(r"\b[0-9a-fA-F]{32}\b"),
    "sha1": re.compile(r"\b[0-9a-fA-F]{40}\b"),
    "sha256": re.compile(r"\b[0-9a-fA-F]{64}\b"),
}
SHA512 = re.compile(r"[0-9a-fA-F]{128}")
ID_HEADER = re.compile(r"(?im)^(?:message-id|in-reply-to|references|content-id|resent-message-id)[ \t]*:.*(?:\r?\n[ \t].*)*")
# 20260312074102.5531@host: a timestamp-style Message-ID, not a mailbox
MESSAGE_ID_LOCAL = re.compile(r"(?=.{10,}$)[0-9]+(?:[.\-_][0-9A-Za-z]+)+")
CVE = re.compile(r"\bCVE-(?:19|20)[0-9]{2}-[0-9]{4,7}\b", re.IGNORECASE)

# -- refang / defang ------------------------------------------------------------

REFANG_RULES = [
    (re.compile(r"\b(h)([xX]{2})(p)(s?)(?=\[?:)", re.IGNORECASE),
     lambda m: m.group(1) + ("TT" if m.group(2).isupper() else "tt") + m.group(3) + m.group(4)),
    (re.compile(r"\b(f)(x)(p)(?=\[?:)", re.IGNORECASE),
     lambda m: m.group(1) + ("T" if m.group(2).isupper() else "t") + m.group(3)),
    (re.compile(r"\[\s*:\s*\]//|\[\s*://\s*\]|\[:/\]/"), lambda m: "://"),
    (re.compile(r"\[\s*:\s*\]"), lambda m: ":"),
    (re.compile(r"\[\s*(?:\.|dot)\s*\]|\(\s*(?:\.|dot)\s*\)|\{\s*(?:\.|dot)\s*\}", re.IGNORECASE), lambda m: "."),
    (re.compile(r"\[\s*(?:@|at)\s*\]|\(\s*(?:@|at)\s*\)|\{\s*(?:@|at)\s*\}", re.IGNORECASE), lambda m: "@"),
]


def refang_text(text: str) -> str:
    for pattern, swap in REFANG_RULES:
        text = pattern.sub(swap, text)
    return text


def tld_ok(domain: str, from_url_or_email: bool = False) -> bool:
    labels = domain.lower().rstrip(".").split(".")
    if len(labels) < 2:
        return False
    tld = labels[-1]
    if tld not in KNOWN_TLDS:
        return False
    if from_url_or_email or tld not in FILE_EXTENSIONS:
        return True
    return len(labels) >= 3 and tld in CC_TLDS


def valid_ipv6(text: str) -> str | None:
    if "::" not in text and text.count(":") != 7 and not ("." in text and text.count(":") >= 2):
        return None
    try:
        addr = ipaddress.IPv6Address(text)
    except ValueError:
        return None
    if int(addr) == 0:
        return None  # "::" alone is noise
    if addr.ipv4_mapped is not None:
        return f"::ffff:{addr.ipv4_mapped}"
    return addr.compressed


def strip_url(url: str) -> str:
    """Drop punctuation that ended the sentence, keeping balanced brackets."""
    while url and url[-1] in ".,;:!?'\")]}>*":
        last = url[-1]
        pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
        if last in pairs and url.count(pairs[last]) >= url.count(last):
            break
        url = url[:-1]
    return url


def url_host(url: str) -> str:
    rest = url.split("://", 1)[1]
    rest = re.split(r"[/?#]", rest, maxsplit=1)[0]
    rest = rest.rsplit("@", 1)[-1]
    if rest.startswith("["):
        return rest[1:].split("]", 1)[0]
    return rest.split(":", 1)[0] if rest.count(":") <= 1 else rest


def find_spans(text: str) -> list:
    """Every IOC in text as (start, end, kind, value), in order of appearance."""
    found = []
    taken = []  # spans of URLs and emails, so their insides aren't double-counted

    def inside(start, end):
        return any(s <= start and end <= e for s, e in taken)

    for m in URL.finditer(text):
        url = strip_url(m.group(0))
        if "://" not in url or not url.split("://", 1)[1]:
            continue
        host = url_host(url)
        if not (IPV4_FULL.fullmatch(host) or valid_ipv6(host) or (DOMAIN_FULL.fullmatch(host) and tld_ok(host, True))):
            continue
        end = m.start() + len(url)
        found.append((m.start(), end, "url", url))
        taken.append((m.start(), end))
        hstart = text.find(host, m.start(), end)
        if IPV4_FULL.fullmatch(host):
            found.append((hstart, hstart + len(host), "ip", host))
        elif DOMAIN_FULL.fullmatch(host):
            found.append((hstart, hstart + len(host), "domain", host.lower()))
        else:
            v6 = valid_ipv6(host)
            if v6:
                found.append((hstart, hstart + len(host), "ip", v6))
    id_headers = [(h.start(), h.end()) for h in ID_HEADER.finditer(text)]
    for m in EMAIL.finditer(text):
        if inside(m.start(), m.end()):
            continue
        if not tld_ok(m.group(1), True):
            continue
        local = m.group(0)[:m.start(1) - m.start() - 1]
        taken.append((m.start(), m.end()))
        found.append((m.start(1), m.end(1), "domain", m.group(1).lower()))
        # Message-ID / References values look like emails but aren't mailboxes
        if any(s <= m.start() < e for s, e in id_headers) or MESSAGE_ID_LOCAL.fullmatch(local):
            continue
        found.append((m.start(), m.end(), "email", f"{local}@{m.group(1).lower()}"))
    for m in IPV4.finditer(text):
        if not inside(m.start(), m.end()):
            found.append((m.start(), m.end(), "ip", m.group(0)))
    for m in IPV6_CANDIDATE.finditer(text):
        if inside(m.start(), m.end()):
            continue
        v6 = valid_ipv6(m.group(0))
        if v6:
            found.append((m.start(), m.end(), "ip", v6))
    for m in DOMAIN.finditer(text):
        if inside(m.start(), m.end()):
            continue
        name = m.group(1)
        if tld_ok(name):
            found.append((m.start(), m.end(), "domain", name.lower()))
    for kind, pattern in HASHES.items():
        for m in pattern.finditer(text):
            value = m.group(0).lower()
            if len(set(value)) > 1:  # skip 000000... placeholders
                found.append((m.start(), m.end(), kind, value))
    for m in CVE.finditer(text):
        found.append((m.start(), m.end(), "cve", m.group(0).upper()))
    found.sort(key=lambda f: (f[0], -f[1]))
    return found


KEYS = {"ip": "ips", "domain": "domains", "url": "urls", "email": "emails",
        "md5": "md5", "sha1": "sha1", "sha256": "sha256", "cve": "cves"}


def extract_text(text: str, refang: bool = True) -> dict:
    if refang:
        text = refang_text(text)
    result = {key: [] for key in KEYS.values()}
    seen = set()
    for _start, _end, kind, value in find_spans(text):
        if (kind, value) in seen:
            continue
        seen.add((kind, value))
        result[KEYS[kind]].append(value)
    return result


def defang_text(text: str) -> str:
    spans = [s for s in find_spans(text) if s[2] in ("url", "email", "ip", "domain")]
    # keep the outermost spans only (a URL already covers its host)
    outer = []
    for start, end, kind, value in sorted(spans, key=lambda s: (s[0], -s[1])):
        if outer and start < outer[-1][1]:
            continue
        outer.append((start, end, kind))
    pieces, pos = [], 0
    for start, end, kind in outer:
        pieces.append(text[pos:start])
        pieces.append(defang_one(text[start:end], kind))
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


def defang_one(value: str, kind: str) -> str:
    if kind == "url":
        scheme, rest = value.split("://", 1)
        scheme = re.sub(r"^(h)(tt)(p)", lambda m: m.group(1) + ("XX" if m.group(2).isupper() else "xx") + m.group(3),
                        scheme, flags=re.IGNORECASE)
        scheme = re.sub(r"^(f)(t)(p)", lambda m: m.group(1) + ("X" if m.group(2).isupper() else "x") + m.group(3),
                        scheme, flags=re.IGNORECASE)
        host_end = len(re.split(r"[/?#]", rest, maxsplit=1)[0])
        host = re.sub(r"\[[0-9A-Fa-f:.]+\]", lambda h: "[" + h.group(0)[1:-1].replace(":", "[:]") + "]",
                      rest[:host_end])
        return f"{scheme}://{host.replace('.', '[.]')}{rest[host_end:]}"
    if kind == "email":
        user, host = value.rsplit("@", 1)
        return f"{user}[@]{host.replace('.', '[.]')}"
    if kind == "ip":
        return value.replace(".", "[.]").replace(":", "[:]")
    return value.replace(".", "[.]")


def kind_of(value: str):
    value = refang_text(value.strip())
    if not value:
        return None
    if IPV4_FULL.fullmatch(value) or valid_ipv6(value):
        return "ip"
    m = URL.fullmatch(value)
    if m and strip_url(value) == value:
        host = url_host(value)
        if IPV4_FULL.fullmatch(host) or valid_ipv6(host) or (DOMAIN_FULL.fullmatch(host) and tld_ok(host, True)):
            return "url"
    m = EMAIL.fullmatch(value)
    if m and tld_ok(m.group(1), True):
        return "email"
    if re.fullmatch(r"[0-9a-fA-F]+", value):
        return {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}.get(len(value))
    if CVE.fullmatch(value):
        return "cve"
    if DOMAIN_FULL.fullmatch(value.rstrip(".")) and tld_ok(value.rstrip(".")):
        return "domain"
    return None


def load(api):
    m = Module(
        "ioc",
        "Indicators of compromise (IOCs): pull IPs, domains, URLs, emails, file hashes and CVE IDs "
        "out of any text, check what something is, and defang them (1.2.3[.]4, hxxp://) so "
        "nobody clicks a bad link by accident.",
    )

    @m.fn("extract", 1, 2, doc=(
        "extract(text, options?) -> map\n"
        "Find every IOC in text: {\"ips\", \"domains\", \"urls\", \"emails\", \"md5\", \"sha1\", \"sha256\", \"cves\"}, "
        "each a list without repeats, in the order they first appear. Defanged IOCs (hxxp://, [.], (.), [@]) are "
        "found too and given back refanged; pass {\"refang\": false} to skip that. File names like report.pdf "
        "are not counted as domains. Domains and IPs inside URLs and emails are listed as well. Email addresses keep "
        "the case of the part before @. Message-ID / References header values are not counted as emails."))
    def extract(line, text, options=None):
        check_type(line, text, "text", "ioc.extract", "text")
        refang = True
        if options is not None:
            check_type(line, options, "map", "ioc.extract", "options")
            refang = options.get("refang", True) not in (False, None)
        return extract_text(text, refang)

    @m.fn("rows", 1, doc=(
        "rows(found) -> list\n"
        "Turn the map from extract() (or the text itself) into a list of {\"type\", \"value\"} maps, one per IOC, "
        "ready for report.table()."))
    def rows(line, found):
        check_type(line, found, "map|text", "ioc.rows", "found")
        if isinstance(found, str):
            found = extract_text(found)
        result = []
        for key in KEYS.values():
            for value in found.get(key, []) or []:
                result.append({"type": key, "value": value})
        return result

    @m.fn("defang", 1, doc=(
        "defang(text) -> text\n"
        "Make the IOCs in text safe to share: http becomes hxxp, dots in hosts become [.], colons in IPv6 addresses "
        "become [:] and @ in emails becomes [@]. "
        "Everything else in the text stays the same. refang() undoes it."))
    def defang(line, text):
        return defang_text(check_type(line, text, "text", "ioc.defang", "text"))

    @m.fn("refang", 1, doc=(
        "refang(text) -> text\n"
        "Undo defanging: hxxp:// and hxxps:// become http(s)://, [.] (.) {.} [dot] become dots, [@] [at] become @, "
        "and [:] becomes a colon."))
    def refang(line, text):
        return refang_text(check_type(line, text, "text", "ioc.refang", "text"))

    @m.fn("is_ip", 1, doc="is_ip(text) -> bool\nTrue for a valid IPv4 (each part 0-255) or IPv6 address. Defanged ones count.")
    def is_ip(line, value):
        check_type(line, value, "text", "ioc.is_ip", "text")
        value = refang_text(value.strip())
        return bool(IPV4_FULL.fullmatch(value) or valid_ipv6(value))

    @m.fn("is_domain", 1, doc="is_domain(text) -> bool\nTrue for a domain name with a real-looking ending, like evil.example.com. "
                              "\"report.pdf\" is a file, so it is false.")
    def is_domain(line, value):
        check_type(line, value, "text", "ioc.is_domain", "text")
        return kind_of(value) == "domain"

    @m.fn("is_hash", 1, doc="is_hash(text) -> bool\nTrue for an MD5, SHA-1, SHA-256 or SHA-512 hash (hex of the right length).")
    def is_hash(line, value):
        check_type(line, value, "text", "ioc.is_hash", "text")
        return kind_of(value) in ("md5", "sha1", "sha256", "sha512")

    @m.fn("kind", 1, doc="kind(text) -> text or nil\nWhat an IOC is: \"ip\", \"url\", \"email\", \"md5\", \"sha1\", \"sha256\", "
                         "\"sha512\", \"cve\" or \"domain\". nil if it's none of them.")
    def kind(line, value):
        check_type(line, value, "text", "ioc.kind", "text")
        return kind_of(value)

    return m
