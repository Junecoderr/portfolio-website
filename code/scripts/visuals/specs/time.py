# Pictures for the time library (read with ast.literal_eval; never run).
# Timestamps are seconds since 1 January 1970; the time line shows where each one sits.
SPEC = {
    "now": {
        "t": "timeline", "call": "time.now()", "keys": ["year", "month", "day", "hour", "minute", "weekday"],
        "marks": [{"label": "1970", "at": 0.0, "under": "timestamp 0"}, {"label": "now", "at": 0.92, "under": "this moment"}],
        "spans": [{"from": 0.0, "to": 0.92, "label": "timestamp = seconds since 1970"}],
        "note": "Real result from the moment the site was built; your program sees the moment it runs.",
    },
    "timestamp": {
        "t": "timeline",
        "marks": [{"label": "1970-01-01", "at": 0.0, "under": "0"}, {"label": "now", "at": 0.92}],
        "spans": [{"from": 0.0, "to": 0.92, "label": "seconds counted (with fractions)"}],
        "note": "Real result from the moment the site was built.",
    },
    "sleep": {
        "t": "timeline", "args": {"seconds": "1.5"}, "run": False,
        "marks": [{"label": "sleep(1.5)", "at": 0.15}, {"label": "goes on", "at": 0.75}],
        "spans": [{"from": 0.15, "to": 0.75, "label": "waits 1.5 seconds", "p": "seconds"}],
        "gives": "nothing; the next line runs 1.5 s later",
        "roles": {"seconds": "how long to wait (0.25 = a quarter second)"},
    },
    "format": {
        "t": "flow", "args": {"timestamp": "1773287045", "pattern": '"%d %B %Y, %H:%M"'},
        "marks": [{"label": "1773287045", "at": 0.6, "p": "timestamp", "under": "a moment in 2026"}],
        "vary": {"param": "pattern", "values": ['"%d %B %Y, %H:%M"', '"%Y-%m-%d"', '"%A %H:%M:%S"', '"%H:%M"'],
                 "labels": ["%d %B %Y, %H:%M", "%Y-%m-%d", "%A %H:%M:%S", "%H:%M"]},
        "roles": {"timestamp": "the moment (seconds since 1970)", "pattern": "the layout: %Y year, %m month, %d day, %H:%M time..."},
        "note": "Real result, in the local time of the computer that built the site.",
    },
    "parse": {
        "t": "flow", "args": {"text": '"2026-03-12 09:14:05"'},
        "marks": [{"label": "\"2026-03-12 09:14:05\"", "at": 0.6, "p": "text", "under": "becomes a number"}],
        "vary": {"param": "text", "values": ['"2026-03-12 09:14:05"', '"2026-03-12"', '"Mar 12 09:14:05"'],
                 "labels": ["2026-03-12 09:14:05", "2026-03-12", "Mar 12 09:14:05"]},
        "roles": {"text": "a date/time written as text", "pattern": "the layout, if it isn't a common one (%Y-%m-%d ...)"},
        "note": "Real result, read in the local time of the computer that built the site.",
    },
    "today": {
        "t": "timeline", "marks": [{"label": "today", "at": 0.92, "under": "year-month-day"}],
        "note": "Real result from the day the site was built.",
    },
    "since": {
        "t": "timeline", "args": {"timestamp": "start"}, "run": False, "static": "90.04",
        "marks": [{"label": "start", "at": 0.3, "p": "timestamp", "under": "let start = time.timestamp()"}, {"label": "now", "at": 0.85}],
        "spans": [{"from": 0.3, "to": 0.85, "label": "seconds that passed"}],
        "roles": {"timestamp": "an earlier time.timestamp()"},
    },
}
