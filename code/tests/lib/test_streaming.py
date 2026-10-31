"""Streaming reads of big files: logs.each / logs.stream, pcap.each and evtx.each read from
disk a piece at a time (also .gz and one-file .zip), so memory stays flat as files grow."""

import gzip
import tracemalloc
import zipfile

from binfixtures import MAC_GW, MAC_WS, ethernet, ipv4, pcap_file, udp
from conftest import run_tb, tb_error
from evtxwriter import Value, build_evtx, security
from tblib import evtx, logs, pcap


def tb_path(p):
    return str(p).replace("\\", "/")


class CountingApi:
    """Stands in for the interpreter: fn(record) just counts, so only the library's memory is measured."""

    def __init__(self):
        self.calls = 0

    def call(self, fn, args, line=None):
        self.calls += 1
        return None


def peak_bytes(work):
    tracemalloc.start()
    try:
        work()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def log_lines(n):
    return "".join(f"Mar 10 09:{i // 60 % 60:02d}:{i % 60:02d} web01 sshd[{1000 + i}]: Failed password for "
                   f"user{i % 50} from 203.0.113.{i % 250} port {40000 + i % 20000} ssh2\n" for i in range(n))


def pcap_bytes(n):
    frame = ethernet(MAC_WS, MAC_GW, 0x0800, ipv4("192.0.2.10", "192.0.2.1", 17, udp(5555, 9999, b"x" * 60)))
    return pcap_file([(1000.0 + i / 100, frame) for i in range(n)])


def evtx_bytes(chunks):
    """A header plus the same full chunk repeated: as big as wanted, quick to build."""
    events = [security(4688, 1773302400.0 + i, "WS01", {"NewProcessName": "C:\\x.exe",
                                                        "CommandLine": f"run {i} " + "a" * 1500},
                       record_id=i + 1, task=13312) for i in range(60)]  # big events: ~20 a chunk
    data = build_evtx(events)
    return data[:4096] + data[4096:4096 + 65536] * chunks


def assert_flat(small, large, small_size, large_size):
    # A reader that held the file (or its records) would grow about tenfold; a streaming one stays put.
    print(f"peak memory: {small:,} bytes for {small_size:,} bytes of input, {large:,} for {large_size:,}")
    assert large < small * 1.5 + 512 * 1024, (small, large)
    assert large < large_size, (large, large_size)  # less than the file itself, let alone its records


def each_peak(module, fn_name, path, *extra):
    api = CountingApi()
    each = module.load(api).fields[fn_name].fn
    peak = peak_bytes(lambda: each(1, path, lambda r: None, *extra))
    return peak, api.calls


def test_logs_each_memory_stays_flat_as_the_file_grows_tenfold(tmp_path):
    small, large = tmp_path / "small.log", tmp_path / "large.log"
    small.write_text(log_lines(1_000))
    large.write_text(log_lines(10_000))
    p_small, n_small = each_peak(logs, "each", str(small))
    p_large, n_large = each_peak(logs, "each", str(large))
    assert (n_small, n_large) == (1_000, 10_000)
    assert_flat(p_small, p_large, small.stat().st_size, large.stat().st_size)
    # The same for a gzip-compressed log, unpacked as it is read.
    packed = tmp_path / "large.log.gz"
    packed.write_bytes(gzip.compress(large.read_bytes()))
    p_packed, n_packed = each_peak(logs, "each", str(packed))
    assert n_packed == 10_000
    assert_flat(p_small, p_packed, small.stat().st_size, large.stat().st_size)


def test_pcap_each_memory_stays_flat_as_the_capture_grows_tenfold(tmp_path):
    small, large = tmp_path / "small.pcap", tmp_path / "large.pcap"
    small.write_bytes(pcap_bytes(1_000))
    large.write_bytes(pcap_bytes(10_000))
    p_small, n_small = each_peak(pcap, "each", str(small))
    p_large, n_large = each_peak(pcap, "each", str(large))
    assert (n_small, n_large) == (1_000, 10_000)
    assert_flat(p_small, p_large, small.stat().st_size, large.stat().st_size)


def test_evtx_each_memory_stays_flat_as_the_log_grows_tenfold(tmp_path):
    small, large = tmp_path / "small.evtx", tmp_path / "large.evtx"
    small.write_bytes(evtx_bytes(2))
    large.write_bytes(evtx_bytes(20))
    p_small, n_small = each_peak(evtx, "each", str(small))
    p_large, n_large = each_peak(evtx, "each", str(large))
    assert n_large == 10 * n_small > 0
    assert_flat(p_small, p_large, small.stat().st_size, large.stat().st_size)


def test_logs_stream_reads_only_as_far_as_the_loop_asks(tmp_path, monkeypatch):
    path = tmp_path / "big.log"
    path.write_text(log_lines(5_000))
    parsed = []
    real = logs.LineParser.parse
    monkeypatch.setattr(logs.LineParser, "parse", lambda self, ln: parsed.append(1) or real(self, ln))
    gen = logs.load(CountingApi()).fields["stream"].fn(1, str(path))
    first, second = next(gen), next(gen)
    assert len(parsed) == 2 and first["user"] == "user0" and second["user"] == "user1"
    out = run_tb(f'import logs\nlet g = logs.stream("{tb_path(path)}")\n'
                 'say(g.take(3).map(fn(r) { return r.user }).to_list())\n'
                 f'let n = 0\nfor r in logs.stream("{tb_path(path)}", "auth", {{"limit": 7}}) {{ n += 1 }}\nsay(n)\n'
                 f'say(logs.stream("{tb_path(path)}").filter(fn(r) {{ return r.user == "user3" }}).take(2).to_list().len())')
    assert out == ['["user0", "user1", "user2"]', "7", "2"]


def test_gz_and_zip_inputs_are_read_transparently(tmp_path):
    text = log_lines(20)
    plain = tmp_path / "auth.log"
    plain.write_text(text)
    gz = tmp_path / "auth.log.gz"
    gz.write_bytes(gzip.compress(text.encode()))
    zipped = tmp_path / "auth.zip"
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("auth.log", text)
    cap = tmp_path / "x.pcap.gz"
    cap.write_bytes(gzip.compress(pcap_bytes(5)))
    ev = tmp_path / "x.evtx.zip"
    with zipfile.ZipFile(ev, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Security.evtx", evtx_bytes(1))
    out = run_tb(
        'import logs\nimport pcap\nimport evtx\n'
        f'say(len(logs.read("{tb_path(plain)}")), len(logs.read("{tb_path(gz)}")), len(logs.read("{tb_path(zipped)}")))\n'
        f'say(logs.read("{tb_path(gz)}")[0].user, logs.sniff("{tb_path(zipped)}"))\n'
        f'say(logs.each("{tb_path(zipped)}", fn(r) {{ return true }}))\n'
        f'say(len(pcap.read("{tb_path(cap)}")), pcap.each("{tb_path(cap)}", fn(p) {{ return true }}))\n'
        f'say(pcap.summary("{tb_path(cap)}").packets)\n'
        f'say(len(evtx.read("{tb_path(ev)}")), evtx.info("{tb_path(ev)}").records, len(logs.read("{tb_path(ev)}")))')
    n = len(list(evtx.iter_records(evtx_bytes(1))))
    assert out == ["20 20 20", "user0 auth", "20", "5 5", "5", f"{n} {n} {n}"]


def test_damaged_or_ambiguous_archives_give_friendly_errors(tmp_path):
    two = tmp_path / "two.zip"
    with zipfile.ZipFile(two, "w") as zf:
        zf.writestr("a.log", "x\n")
        zf.writestr("b.log", "y\n")
    assert "unzip it first" in str(tb_error(f'import logs\nlogs.read("{tb_path(two)}")'))
    cut = tmp_path / "cut.log.gz"
    cut.write_bytes(gzip.compress(log_lines(2000).encode())[:-200])
    assert "damaged compressed file" in str(tb_error(f'import logs\nlogs.each("{tb_path(cut)}", fn(r) {{ return true }})'))
    bad = tmp_path / "bad.pcap.gz"
    bad.write_bytes(b"\x1f\x8b" + b"\x00" * 30)
    assert "damaged compressed file" in str(tb_error(f'import pcap\npcap.read("{tb_path(bad)}")'))
    broken_zip = tmp_path / "broken.evtx.zip"
    broken_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    assert "zip I can't open" in str(tb_error(f'import evtx\nevtx.read("{tb_path(broken_zip)}")'))
