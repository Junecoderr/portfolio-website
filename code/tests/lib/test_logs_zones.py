"""logs: records from logs in different time zones carry "zone" and "when_utc",
so they sort correctly together (the mixed-zone ordering fix)."""

from conftest import run_tb


def test_mixed_zone_records_sort_by_when_utc():
    out = run_tb('''
import logs
let rs = logs.parse("2024-03-10T09:15:02+02:00 host sshd: a\\n2024-03-10T08:00:00Z host sshd: b\\n10/Mar/2024:09:30:00 +0100 x")
for r in rs { say(r.when + " | " + r.when_utc + " | " + r.zone) }
say(rs.sort_by(fn(r) { return r.when_utc }).map(fn(r) { return r.when_utc }))
''')
    assert out[:3] == [
        "2024-03-10 09:15:02 | 2024-03-10 07:15:02 | +02:00",
        "2024-03-10 08:00:00 | 2024-03-10 08:00:00 | UTC",
        "2024-03-10 09:30:00 | 2024-03-10 08:30:00 | +01:00",
    ]
    assert out[3] == '["2024-03-10 07:15:02", "2024-03-10 08:00:00", "2024-03-10 08:30:00"]'


def test_record_without_time_has_nil_when_utc():
    out = run_tb('import logs\nlet r = logs.parse_line("just some text")\nsay(r.when_utc)\nsay(r.zone)')
    assert out == ["nil", "nil"]
