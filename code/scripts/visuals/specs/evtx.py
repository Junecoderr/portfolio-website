# Pictures for the evtx library (read with ast.literal_eval; never run).
# Every example runs through TB on apps/data/sample.evtx (24 Windows events) while the site builds.
SPEC = {
    "_default": {"modes": {"path": "lines"}},
    "read": {
        "args": {"path": '"apps/data/sample.evtx"', "options": '{"limit": 3}'},
        "keys": ["event_id", "action", "computer"],
        "nodes": [{"param": "path", "icon": "file"}, {"param": "options"}, {"arrow": "evtx.read"},
                  {"result": True, "label": "result · records, like the logs library gives"}],
        "vary": {"param": "options", "values": ['{"limit": 1}', '{"limit": 3}', '{"limit": 5}'], "labels": ["limit 1", "limit 3", "limit 5"],
                 "default": '{"limit": 3}'},
        "roles": {"path": "a Windows .evtx event log", "options": "limit, xml (add the event's XML), start..."},
    },
    "parse": {
        "setup": 'let data = fs.read_bytes("apps/data/sample.evtx")', "imports": ["fs"],
        "args": {"data": "data", "options": '{"limit": 2}'},
        "keys": ["event_id", "action", "computer"],
        "nodes": [{"row": [{"param": "data", "icon": "cpu", "lines": ["EVTX bytes", "\"ElfFile\" …"]}, {"param": "options"}]},
                  {"arrow": "evtx.parse"}, {"result": True}],
        "roles": {"data": "EVTX bytes already in memory", "options": "the same options as read()"},
    },
    "each": {
        "setup": "let ids = []",
        "args": {"path": '"apps/data/sample.evtx"', "fn": "r => ids.push(r.event_id)"},
        "nodes": [{"param": "path", "icon": "file"},
                  {"box": "one event at a time", "icon": "list", "lines": ["event 1 → fn", "… event 24 → fn"]},
                  {"param": "fn"}, {"param": "options", "lines": ["limit, start..."]}, {"arrow": "evtx.each"},
                  {"result": True, "label": "result · how many events"}],
        "roles": {"path": "the .evtx file", "fn": "runs for every event", "options": "the same options as read()"},
    },
    "summary": {
        "args": {"path_or_records": '"apps/data/sample.evtx"'},
        "keys": ["records", "first_when", "last_when", "event_ids", "actions"],
        "nodes": [{"param": "path_or_records", "icon": "file", "mode": "lines"}, {"arrow": "evtx.summary"},
                  {"result": True, "label": "result · an overview: counts per event id and action"}],
        "roles": {"path_or_records": "an .evtx file or records you already read"},
    },
    "info": {
        "args": {"path": '"apps/data/sample.evtx"'},
        "keys": ["version", "chunk_count", "next_record_id", "dirty", "full", "checksums_ok"],
        "nodes": [{"param": "path", "icon": "file"}, {"box": "file header", "kind": "plain", "lines": ["ElfFile ... chunks ... checksums"]},
                  {"arrow": "evtx.info"}, {"result": True}],
        "roles": {"path": "the .evtx file"},
    },
    "is_evtx": {
        "args": {"path": '"apps/data/sample.evtx"'},
        "vary": {"param": "path", "values": ['"apps/data/sample.evtx"', '"apps/data/sample.pcap"'], "labels": ["sample.evtx", "sample.pcap"]},
        "roles": {"path": "the file to check"},
    },
    "to_json": {
        "setup": 'let r = evtx.read("apps/data/sample.evtx", {"limit": 1})[0]',
        "args": {"record": "r"},
        "mode": "lines",
        "nodes": [{"param": "record", "icon": "doc", "lines": ["{event_id: 4624, action: login,", " computer: WS-FINANCE-07 ...}"]},
                  {"arrow": "evtx.to_json"}, {"result": True, "label": "result · one JSON line"}],
        "roles": {"record": "one record from read()"},
    },
}
