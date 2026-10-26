import json

import pytest

import tb
from conftest import run_tb, tb_error
from tblib.ioc import defang_text, extract_text, refang_text


def extract(text, **kw):
    return extract_text(text, **kw)


# -- IPs ---------------------------------------------------------------------------

def test_ipv4_valid_octets_only():
    found = extract("ok 203.0.113.5 and 10.0.0.1; bad 256.1.1.1 999.10.10.10 1.2.3 and 01.2.3.4")
    assert found["ips"] == ["203.0.113.5", "10.0.0.1"]


def test_ipv4_not_inside_longer_dotted_numbers():
    found = extract("version 1.2.3.4.5 and oid 1.3.6.1.4.1 but 198.51.100.7. ends a sentence")
    assert found["ips"] == ["198.51.100.7"]


def test_ipv4_with_port_and_brackets():
    assert extract("[203.0.113.9]:443 and (192.0.2.1)")["ips"] == ["203.0.113.9", "192.0.2.1"]


def test_ipv6_valid_and_compressed():
    found = extract("v6 2001:DB8:0:0:0:0:0:1 and fe80::1%eth0 and ::1 and 2001:db8::1")
    assert found["ips"] == ["2001:db8::1", "fe80::1", "::1"]


def test_ipv6_false_positives():
    text = "time 12:30:45, mac 00:1a:2b:3c:4d:5e, std::vector, ratio 3:2, Mar 12 09:14:05"
    assert extract(text)["ips"] == []


# -- domains -----------------------------------------------------------------------

def test_file_names_are_not_domains():
    text = "Open report.pdf, run tb.py, load jquery.min.js, invoice.zip, setup.exe, notes.txt and archive.tar.gz"
    assert extract(text)["domains"] == []


def test_domains_need_a_real_tld():
    found = extract("evil.example.com, www.example.pl, CDN.Example.NET, foo.bar, config.local, a.b")
    assert found["domains"] == ["evil.example.com", "www.example.pl", "cdn.example.net", "config.local"]


def test_domain_with_file_extension_tld_counts_inside_urls():
    found = extract("download from https://files.example.zip/a and see readme.md")
    assert found["urls"] == ["https://files.example.zip/a"]
    assert found["domains"] == ["files.example.zip"]


def test_code_like_names_and_versions_are_not_domains():
    assert extract("call os.path.join and self.value, version v2.4.1, e.g. this")["domains"] == []


# -- URLs and emails ---------------------------------------------------------------

def test_urls_trailing_punctuation_and_parens():
    text = "See http://example.com/a_(b). Then (https://evil.example.net/x?y=1), and ftp://198.51.100.7/f.bin!"
    assert extract(text)["urls"] == [
        "http://example.com/a_(b)", "https://evil.example.net/x?y=1", "ftp://198.51.100.7/f.bin"]


def test_url_hosts_are_listed_as_domains_and_ips():
    found = extract("http://198.51.100.7:8080/a.exe and https://cdn.example.com/x.js")
    assert found["ips"] == ["198.51.100.7"]
    assert found["domains"] == ["cdn.example.com"]


def test_url_with_ipv6_host():
    found = extract("http://[2001:db8::2]:8080/x")
    assert found["urls"] == ["http://[2001:db8::2]:8080/x"] and found["ips"] == ["2001:db8::2"]


def test_emails():
    found = extract("Mail Billing@Paypa1-Secure.example.org or x@y, not user@host or a@b.pdf")
    assert found["emails"] == ["Billing@paypa1-secure.example.org"]
    assert found["domains"] == ["paypa1-secure.example.org"]


def test_email_local_part_keeps_its_case_and_dedupes_by_domain_case():
    found = extract("First.Last+tag@Corp.Example.com and First.Last+tag@corp.example.COM")
    assert found["emails"] == ["First.Last+tag@corp.example.com"]


def test_message_id_headers_are_not_emails():
    text = ("Message-ID: <20260312074102.5531@paypa1-secure.example.org>\n"
            "In-Reply-To: <CAF=x1@mail.example.com>\nReferences: <a.b@x.example.net>\n <c@y.example.net>\n"
            "From: alice@example.org\nBody mentions 20260312074102.5531@other.example.com and 12345@example.com")
    found = extract(text)
    assert found["emails"] == ["alice@example.org", "12345@example.com"]
    # the domains are still useful IOCs
    assert found["domains"][:4] == ["paypa1-secure.example.org", "mail.example.com", "x.example.net", "y.example.net"]


def test_ipv6_defang_round_trips():
    text = "beacon to 2001:db8:4b::17, ::ffff:192.0.2.1 and http://[2001:db8::2]:8080/x"
    defanged = defang_text(text)
    assert defanged == ("beacon to 2001[:]db8[:]4b[:][:]17, [:][:]ffff[:]192[.]0[.]2[.]1 and "
                        "hxxp://[2001[:]db8[:][:]2]:8080/x")
    assert refang_text(defanged) == text
    assert extract(defanged) == extract(text)
    assert extract(text)["ips"] == ["2001:db8:4b::17", "::ffff:192.0.2.1", "192.0.2.1", "2001:db8::2"]


# -- hashes and CVEs ---------------------------------------------------------------

def test_hashes_by_exact_length():
    md5 = "44d88612fea8a8f36de82e1278abb02f"
    sha1 = "3395856ce81f2b7382dee72602f798b642f14140"
    sha256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"
    text = f"{md5.upper()} {sha1} {sha256} too-long {md5}ab {'0' * 32} x{md5}"
    found = extract(text)
    assert found["md5"] == [md5] and found["sha1"] == [sha1] and found["sha256"] == [sha256]


def test_cves():
    assert extract("cve-2021-44228, CVE-2023-23397 and CVE-2024-123")["cves"] == ["CVE-2021-44228", "CVE-2023-23397"]


# -- order, dedupe, defang/refang ---------------------------------------------------

def test_dedupes_in_order_of_first_appearance():
    found = extract("b.example.com 192.0.2.2 a.example.com 192.0.2.1 B.EXAMPLE.COM 192.0.2.2")
    assert found["domains"] == ["b.example.com", "a.example.com"]
    assert found["ips"] == ["192.0.2.2", "192.0.2.1"]


def test_defanged_input_is_refanged():
    text = "hxxps://login[.]example[.]com/x and 203.0.113[.]5 and evil(.)example(.)net, mail bob[@]example[.]org hXXp[:]//a[dot]example[dot]com"
    found = extract(text)
    assert found["urls"] == ["https://login.example.com/x", "hTTp://a.example.com"]
    assert found["ips"] == ["203.0.113.5"]
    assert "evil.example.net" in found["domains"]
    assert found["emails"] == ["bob@example.org"]
    assert extract(text, refang=False)["urls"] == []


def test_defang_then_refang_round_trips():
    text = ("Payload at https://evil.example.com/a.php?x=1 from 203.0.113.55 / 2001:db8::7, mail Admin@Corp.example.org, "
            "HTTP://UPPER.example.net/ and ftp://198.51.100.9/x. Files report.pdf and tb.py stay as they are.")
    defanged = defang_text(text)
    assert "hxxps://evil[.]example[.]com/a.php?x=1" in defanged
    assert "203[.]0[.]113[.]55" in defanged and "2001[:]db8[:][:]7" in defanged
    assert "Admin[@]Corp[.]example[.]org" in defanged
    assert "HXXP://UPPER[.]example[.]net/" in defanged
    assert "fxp://198[.]51[.]100[.]9/x" in defanged
    assert "report.pdf and tb.py" in defanged
    assert extract(defanged) == extract(text)
    assert refang_text(defanged) == text


def test_extract_from_tb_and_rows():
    src = '''import ioc
let found = ioc.extract("Callback to hxxp://198.51.100.14/x and evil[.]example[.]com, CVE-2023-23397")
say(found.urls, found.ips, found.domains, found.cves)
say(ioc.rows(found))
say(len(ioc.rows("1.2.3.4 and 1.2.3.4")))'''
    assert run_tb(src) == [
        '["http://198.51.100.14/x"] ["198.51.100.14"] ["evil.example.com"] ["CVE-2023-23397"]',
        '[{"type": "ips", "value": "198.51.100.14"}, {"type": "domains", "value": "evil.example.com"}, '
        '{"type": "urls", "value": "http://198.51.100.14/x"}, {"type": "cves", "value": "CVE-2023-23397"}]',
        "1",
    ]


def test_every_key_is_always_present():
    src = 'import ioc\nsay(keys(ioc.extract("nothing here")))'
    assert run_tb(src) == ['["ips", "domains", "urls", "emails", "md5", "sha1", "sha256", "cves"]']


def test_checks_and_kind():
    src = '''import ioc
say(ioc.is_ip("192.0.2.1"), ioc.is_ip("192.0.2[.]1"), ioc.is_ip("2001:db8::1"), ioc.is_ip("300.1.1.1"), ioc.is_ip("1.2.3"))
say(ioc.is_domain("evil.example.com"), ioc.is_domain("report.pdf"), ioc.is_domain("localhost"), ioc.is_domain("a.b"))
say(ioc.is_hash("d41d8cd98f00b204e9800998ecf8427e"), ioc.is_hash("xyz"), ioc.is_hash("d41d8cd98f00b204e9800998ecf8427"))
say(ioc.kind("1.2.3.4"), ioc.kind("hxxps://a.example.com/x"), ioc.kind("a@example.com"), ioc.kind("CVE-2020-1234"))
say(ioc.kind("d41d8cd98f00b204e9800998ecf8427e"), ioc.kind("da39a3ee5e6b4b0d3255bfef95601890afd80709"), ioc.kind("example[.]org"), ioc.kind("hello"), ioc.kind(""))'''
    assert run_tb(src) == [
        "true true true false false",
        "true false false false",
        "true false false",
        "ip url email cve",
        "md5 sha1 domain nil nil",
    ]


def test_defang_and_refang_from_tb():
    src = 'import ioc\nsay(ioc.defang("go to http://evil.example.com now"))\nsay(ioc.refang("hxxp://evil[.]example[.]com"))'
    assert run_tb(src) == ["go to hxxp://evil[.]example[.]com now", "http://evil.example.com"]


def test_sample_phishing_email(root):
    text = (root / "apps" / "data" / "phishing_email.txt").read_text()
    found = extract(text)
    assert found["ips"][:2] == ["203.0.113.55", "198.51.100.14"]
    assert "login-paypa1.account-verify.example.net" in found["domains"]
    assert "update-check.cdn-example.com" in found["domains"]
    assert not any(d.endswith((".pdf", ".py", ".exe", ".ps1", ".zip")) for d in found["domains"])
    assert found["cves"] == ["CVE-2023-23397"]
    assert found["md5"] == ["5d41402abc4b2a76b9719d911017c592"]
    json.dumps(found)


@pytest.mark.parametrize("src,message", [
    ("ioc.extract(5)", "ioc.extract() needs text to be text, not a number."),
    ('ioc.extract("x", 1)', "ioc.extract() needs options to be a map"),
    ("ioc.defang(nil)", "ioc.defang() needs text to be text, not nil."),
    ("ioc.kind([])", "ioc.kind() needs text to be text, not a list."),
    ("ioc.rows(3)", "ioc.rows() needs found to be a map or text"),
])
def test_ioc_errors(src, message):
    assert message in str(tb_error(f"import ioc\n{src}"))
