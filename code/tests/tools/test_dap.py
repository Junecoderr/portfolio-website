"""`tb debug --dap`: the Debug Adapter Protocol server, driven the way VS Code drives it.

The client below spawns `tb debug --dap` as a subprocess and speaks
Content-Length framed JSON over its stdin/stdout, sending the same requests in
the same order as VS Code's debug UI: initialize, launch, setBreakpoints,
configurationDone, then threads/stackTrace/scopes/variables/evaluate at each
stop, and continue/next/stepIn/stepOut until the program terminates.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PROGRAM = """fn square(n) {
  let result = n * n
  return result
}

let total = 0
let names = ["a", "b"]
for i in [1, 2, 3] {
  total += square(i)
}
say(f"total {total}")
"""

HELPER = """fn twice(x) {
  return x * 2
}
"""

MAIN = """import "helper.tb" as helper
let value = helper.twice(21)
say(value)
"""


class DapClient:
    def __init__(self, cwd, engine):
        env = dict(os.environ, TB_ENGINE=engine)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "tb.py"), "debug", "--dap"], cwd=cwd, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.seq = 0
        self.inbox: queue.Queue = queue.Queue()
        self.events: list[dict] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        out = self.proc.stdout
        while True:
            length = None
            while True:
                line = out.readline()
                if not line:
                    self.inbox.put(None)
                    return
                line = line.strip()
                if not line:
                    break
                name, _, value = line.decode("ascii").partition(":")
                if name.lower() == "content-length":
                    length = int(value)
            self.inbox.put(json.loads(out.read(length).decode("utf-8")))

    def next_message(self) -> dict:
        message = self.inbox.get(timeout=60)
        assert message is not None, "the adapter closed its output: " + self.proc.stderr.read().decode()
        return message

    def send(self, command: str, arguments=None) -> int:
        self.seq += 1
        body = json.dumps({"seq": self.seq, "type": "request", "command": command,
                           "arguments": arguments or {}}).encode("utf-8")
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        self.proc.stdin.flush()
        return self.seq

    def request(self, command: str, arguments=None, ok=True) -> dict:
        seq = self.send(command, arguments)
        while True:
            message = self.next_message()
            if message["type"] == "response" and message["request_seq"] == seq:
                assert message["command"] == command
                if ok:
                    assert message["success"], message
                return message
            self.events.append(message)

    def wait_event(self, name: str) -> dict:
        for k, message in enumerate(self.events):
            if message.get("event") == name:
                return self.events.pop(k)
        while True:
            message = self.next_message()
            if message.get("event") == name:
                return message
            self.events.append(message)

    def output(self) -> str:
        return "".join(e["body"]["output"] for e in self.events if e.get("event") == "output")

    def close(self) -> int:
        try:
            self.proc.stdin.close()   # like an editor going away: the adapter must exit
            return self.proc.wait(timeout=30)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.stdout.close()
            self.proc.stderr.close()


def same_file(a: str, b) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(str(b)))


def start(client: DapClient, program: Path, breakpoints: dict, **launch) -> dict:
    """initialize -> launch -> setBreakpoints -> configurationDone, as VS Code does."""
    init = client.request("initialize", {"clientID": "vscode", "adapterID": "tb", "linesStartAt1": True,
                                         "columnsStartAt1": True, "pathFormat": "path"})
    assert init["body"]["supportsConfigurationDoneRequest"] is True
    client.wait_event("initialized")
    client.request("launch", {"type": "tb", "request": "launch", "name": "Debug TB file",
                              "program": str(program), "cwd": str(program.parent), **launch})
    answers = {}
    for path, lines in breakpoints.items():
        answers[path] = client.request("setBreakpoints", {
            "source": {"path": str(path), "name": Path(path).name},
            "breakpoints": [{"line": n} for n in lines]})["body"]["breakpoints"]
    client.request("setExceptionBreakpoints", {"filters": []})
    client.request("configurationDone")
    return answers


def variables(client: DapClient, ref: int) -> dict:
    found = client.request("variables", {"variablesReference": ref})["body"]["variables"]
    return {v["name"]: v for v in found}


def stop_details(client: DapClient):
    """What VS Code asks for after each `stopped` event."""
    assert client.request("threads")["body"]["threads"] == [{"id": 1, "name": "main"}]
    frames = client.request("stackTrace", {"threadId": 1, "startFrame": 0, "levels": 20})["body"]["stackFrames"]
    scopes = client.request("scopes", {"frameId": frames[0]["id"]})["body"]["scopes"]
    by_name = {s["name"]: s for s in scopes}
    return frames, variables(client, by_name["Locals"]["variablesReference"]), \
        variables(client, by_name["Globals"]["variablesReference"])


@pytest.mark.parametrize("engine", ["vm", "tree"])
def test_breakpoint_variables_step_continue_to_the_end(tmp_path, engine):
    program = tmp_path / "prog.tb"
    program.write_text(PROGRAM, encoding="utf-8")
    client = DapClient(tmp_path, engine)
    try:
        answers = start(client, program, {program: [2, 4]})
        assert [b["verified"] for b in answers[program]] == [True, False]   # line 4 is blank

        stopped = client.wait_event("stopped")["body"]
        assert stopped["reason"] == "breakpoint" and stopped["threadId"] == 1
        frames, local_vars, global_vars = stop_details(client)
        assert frames[0]["line"] == 2 and frames[0]["name"] == "square()"
        assert same_file(frames[0]["source"]["path"], program)
        assert [f["name"] for f in frames] == ["square()", "<main>"] and frames[1]["line"] == 9
        assert local_vars["n"]["value"] == "1" and local_vars["n"]["type"] == "number"
        assert global_vars["total"]["value"] == "0"
        names = global_vars["names"]
        assert names["variablesReference"] > 0
        assert {k: v["value"] for k, v in variables(client, names["variablesReference"]).items()} == \
            {"[0]": '"a"', "[1]": '"b"'}
        evaluated = client.request("evaluate", {"expression": "n * 10", "frameId": frames[0]["id"],
                                                "context": "watch"})["body"]
        assert evaluated["result"] == "10"
        bad = client.request("evaluate", {"expression": "nope + 1", "context": "repl"}, ok=False)
        assert bad["success"] is False and "nope" in bad["message"]

        client.request("next", {"threadId": 1})
        assert client.wait_event("stopped")["body"]["reason"] == "step"
        frames, local_vars, _ = stop_details(client)
        assert frames[0]["line"] == 3 and local_vars["result"]["value"] == "1"

        client.request("continue", {"threadId": 1})
        assert client.wait_event("stopped")["body"]["reason"] == "breakpoint"
        frames, local_vars, _ = stop_details(client)
        assert frames[0]["line"] == 2 and local_vars["n"]["value"] == "2"

        client.request("setBreakpoints", {"source": {"path": str(program)}, "breakpoints": []})
        client.request("continue", {"threadId": 1})
        assert client.wait_event("exited")["body"]["exitCode"] == 0
        client.wait_event("terminated")
        assert "total 14\n" in client.output()
        client.request("disconnect", {"restart": False})
    finally:
        code = client.close()
    assert code == 0


@pytest.mark.parametrize("engine", ["vm", "tree"])
def test_stop_on_entry_step_in_and_out_across_files(tmp_path, engine):
    (tmp_path / "helper.tb").write_text(HELPER, encoding="utf-8")
    program = tmp_path / "main.tb"
    program.write_text(MAIN, encoding="utf-8")
    client = DapClient(tmp_path, engine)
    try:
        start(client, program, {}, stopOnEntry=True)
        assert client.wait_event("stopped")["body"]["reason"] == "entry"
        frames, _, _ = stop_details(client)
        assert frames[0]["line"] == 1
        client.request("next", {"threadId": 1})
        client.wait_event("stopped")
        assert stop_details(client)[0][0]["line"] == 2
        client.request("stepIn", {"threadId": 1})
        client.wait_event("stopped")
        frames, local_vars, _ = stop_details(client)
        assert same_file(frames[0]["source"]["path"], tmp_path / "helper.tb")
        assert frames[0]["line"] == 2 and local_vars["x"]["value"] == "21"
        client.request("stepOut", {"threadId": 1})
        client.wait_event("stopped")
        frames, _, _ = stop_details(client)
        assert same_file(frames[0]["source"]["path"], program) and frames[0]["line"] == 3
        client.request("continue", {"threadId": 1})
        client.wait_event("terminated")
        assert "42\n" in client.output()
        client.request("disconnect")
    finally:
        code = client.close()
    assert code == 0


def test_breakpoint_in_an_imported_file_and_friendly_launch_errors(tmp_path):
    helper = tmp_path / "helper.tb"
    helper.write_text(HELPER, encoding="utf-8")
    program = tmp_path / "main.tb"
    program.write_text(MAIN, encoding="utf-8")
    client = DapClient(tmp_path, "vm")
    try:
        client.request("initialize", {"adapterID": "tb"})
        bad = client.request("launch", {"program": str(tmp_path / "missing.tb")}, ok=False)
        assert "couldn't find the file" in bad["message"]
        client.request("launch", {"program": str(program)})
        client.request("setBreakpoints", {"source": {"path": str(helper)}, "breakpoints": [{"line": 2}]})
        client.request("configurationDone")
        assert client.wait_event("stopped")["body"]["reason"] == "breakpoint"
        frames, local_vars, _ = stop_details(client)
        assert same_file(frames[0]["source"]["path"], helper) and local_vars["x"]["value"] == "21"
        # disconnecting while paused ends the program right away
        client.request("disconnect", {"terminateDebuggee": True})
    finally:
        code = client.close()
    assert code == 0
    assert "42" not in client.output()
