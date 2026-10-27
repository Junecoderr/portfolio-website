# Pictures for the datetime library (read with ast.literal_eval; never run).
# A datetime is a moment plus a time zone; the pictures show the same moment on a time line.
SPEC = {
    "_default": {"t": "flow", "keys": ["iso", "zone", "weekday", "timestamp"],
                 "roles": {"zone": "a time zone like \"Europe/Berlin\", or an offset like \"+05:30\""}},
    "now": {
        "args": {"zone": '"UTC"'}, "mode": "tree",
        "marks": [{"label": "now", "at": 0.8, "under": "one moment, shown in the zone"}],
        "vary": {"param": "zone", "values": ['"UTC"', '"Asia/Kolkata"', '"America/New_York"'],
                 "labels": ["UTC", "Asia/Kolkata", "America/New_York"]},
        "roles": {"zone": "which zone's clock to read (this computer's if left out)"},
        "note": "Real result from the moment the site was built.",
    },
    "parse": {
        "args": {"text": '"26/10/2026 14:30"', "pattern": '"%d/%m/%Y %H:%M"', "zone": '"Europe/Berlin"'},
        "marks": [{"label": "14:30 on 26 Oct 2026", "at": 0.6, "p": "text"}],
        "vary": {"param": "zone", "values": ['"Europe/Berlin"', '"UTC"', '"+05:30"'], "labels": ["Europe/Berlin", "UTC", "+05:30"]},
        "roles": {"text": "a date written as text", "pattern": "its layout: %d day, %m month, %Y year, %H:%M",
                  "zone": "the zone to assume when the text has none"},
    },
    "from_timestamp": {
        "args": {"timestamp": "1773287045", "zone": '"UTC"'},
        "marks": [{"label": "1773287045", "at": 0.6, "p": "timestamp", "under": "seconds since 1970"}],
        "vary": {"param": "zone", "values": ['"UTC"', '"Asia/Kolkata"', '"America/Los_Angeles"'],
                 "labels": ["UTC", "Asia/Kolkata", "America/Los_Angeles"]},
        "roles": {"timestamp": "seconds since 1 January 1970", "zone": "the zone to show it in"},
    },
    "make": {
        "args": {"parts": '{"year": 2026, "month": 10, "day": 26, "hour": 9}', "zone": '"Europe/Berlin"'},
        "marks": [{"label": "26 Oct 2026, 09:00", "at": 0.6, "p": "parts"}],
        "vary": {"param": "zone", "values": ['"Europe/Berlin"', '"UTC"', '"Asia/Tokyo"'], "labels": ["Europe/Berlin", "UTC", "Asia/Tokyo"]},
        "roles": {"parts": "year, month, day, hour, minute, second", "zone": "where that wall-clock time is"},
    },
    "today": {
        "args": {"zone": '"UTC"'}, "keys": None,
        "marks": [{"label": "today", "at": 0.85, "p": "zone"}],
        "roles": {"zone": "whose today (it can differ across the date line)"},
        "note": "Real result from the day the site was built.",
    },
    "zones": {
        "args": {"filter": '"Asia/K"'}, "keys": None,
        "marks": [{"label": "UTC+0", "at": 0.1}, {"label": "Asia/Karachi +5", "at": 0.5}, {"label": "Asia/Kolkata +5:30", "at": 0.85}],
        "axis": "east of UTC →",
        "vary": {"param": "filter", "values": ['"Asia/K"', '"Europe/B"', '"Pacific/A"'], "labels": ["Asia/K", "Europe/B", "Pacific/A"]},
        "roles": {"filter": "only names containing this text"},
    },
    "is_zone": {
        "args": {"name": '"Europe/Berlin"'}, "keys": None,
        "marks": [{"label": "zone?", "at": 0.5, "p": "name"}],
        "vary": {"param": "name", "values": ['"Europe/Berlin"', '"+02:00"', '"Mars/Olympus"'], "labels": ["Europe/Berlin", "+02:00", "Mars/Olympus"]},
        "roles": {"name": "a zone name or an offset"},
    },
    "is_datetime": {
        "args": {"x": 'datetime.now("UTC")'}, "keys": None,
        "marks": [{"label": "x", "at": 0.5, "p": "x"}],
        "vary": {"param": "x", "values": ['datetime.now("UTC")', '"2026-10-26"', '1773287045'], "labels": ["datetime.now()", "\"2026-10-26\"", "1773287045"]},
        "roles": {"x": "any value"},
    },
    "local_zone": {
        "t": "timeline", "keys": None, "run": False, "static": '"+05:30"',
        "marks": [{"label": "UTC", "at": 0.3}, {"label": "this computer", "at": 0.6, "under": "+05:30 ahead"}],
        "spans": [{"from": 0.3, "to": 0.6, "label": "the offset"}],
        "gives": "an offset like \"+05:30\"",
    },
}
