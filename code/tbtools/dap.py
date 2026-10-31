"""`tb debug --dap`: the TB debugger as a Debug Adapter Protocol server.

Editors (the VS Code extension's "tb" debug type) start it and speak DAP over
stdin/stdout: Content-Length framed JSON, as in the official specification.
Requests: initialize, launch, setBreakpoints, setExceptionBreakpoints,
configurationDone, threads, stackTrace, scopes, variables, continue, next,
stepIn, stepOut, pause, evaluate, disconnect, terminate. Events: initialized,
stopped, output, exited, terminated.

It reuses `tbtools.debug.Debugger` (the trace hook, stepping and scopes), so it
works the same on both engines. The program runs on the main thread; a reader
thread takes the editor's messages. While the program is paused, requests that
need its state (stackTrace, variables, evaluate, continue, ...) are handed to
the main thread. The program's output becomes `output` events; ask() gets no
input (it returns "").
"""

from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading

from tbtools.debug import Debugger, _Quit

__all__ = ["DapServer", "main"]

THREAD_ID = 1
RESUME = {"continue": "continue", "next": "next", "stepIn": "step", "stepOut": "out"}
# requests the reader thread answers itself, paused or not
CONTROL = {"initialize", "launch", "attach", "setBreakpoints", "setExceptionBreakpoints",
           "setFunctionBreakpoints", "configurationDone", "threads", "pause", "disconnect", "terminate"}
_STOP = object()


class _Scope(dict):
    """The names of a scope (Locals, Globals), as opposed to a TB map."""


class _Events(io.TextIOBase):
    """sys.stdout / sys.stderr while the program runs: text becomes output events."""

    def __init__(self, server, category):
        self.server, self.category = server, category

    def writable(self):
        return True

    def write(self, text):
        if text:
            self.server.event("output", {"category": self.category, "output": text})
        return len(text)


class DapServer(Debugger):
    def __init__(self, reader=None, writer=None):
        super().__init__("program.tb", [])
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        self.seq = 0
        self.write_lock = threading.Lock()
        self.work: queue.Queue = queue.Queue()
        self.bps: dict[str, set[int]] = {}
        self.launched = self.configured = False
        self.ready = threading.Event()        # launch + configurationDone (or disconnect)
        self.finished = threading.Event()     # disconnect or end of input
        self.quitting = False
        self.paused = False
        self.pause_asked = False
        self.entry = False
        self.refs: list = []
        self.cwd = None
        self.no_debug = False

    # -- transport ----------------------------------------------------------------

    def read_message(self) -> dict | None:
        length = None
        while True:
            line = self.reader.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                if length is not None:
                    break
                continue
            name, _, value = line.decode("ascii", "replace").partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
        body = self.reader.read(length)
        if body is None or len(body) < length:
            return None
        return json.loads(body.decode("utf-8"))

    def send(self, message: dict) -> None:
        with self.write_lock:
            self.seq += 1
            message["seq"] = self.seq
            body = json.dumps(message, ensure_ascii=False).encode("utf-8")
            self.writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            self.writer.flush()

    def event(self, name: str, body: dict | None = None) -> None:
        self.send({"type": "event", "event": name, "body": body or {}})

    def respond(self, request: dict, body=None, error: str | None = None) -> None:
        message = {"type": "response", "request_seq": request.get("seq", 0),
                   "command": request.get("command", ""), "success": error is None}
        if error is not None:
            message["message"] = error
            message["body"] = {"error": {"id": 1, "format": error, "showUser": True}}
        elif body is not None:
            message["body"] = body
        self.send(message)

    # -- what the Debugger base class calls ------------------------------------------

    def say(self, text: str = "") -> None:
        self.event("output", {"category": "console", "output": text + "\n"})

    def make_interpreter(self):
        import tb

        def ask(prompt=""):
            if prompt:
                self.event("output", {"category": "stdout", "output": str(prompt)})
            raise EOFError   # the editor owns stdin

        return tb.Interpreter(
            output=lambda text: self.event("output", {"category": "stdout", "output": f"{text}\n"}),
            input_fn=ask)

    def at_breakpoint(self, file, line) -> bool:
        lines = self.bps.get(self.key(file or self.path))
        return bool(lines) and line in lines

    def stopped_at(self, file, line) -> None:
        if self.quitting:
            raise _Quit()
        if self.pause_asked:
            reason = "pause"
        elif self.at_breakpoint(file, line):
            reason = "breakpoint"
        elif self.entry:
            reason = "entry"
        else:
            reason = "step"
        self.pause_asked = self.entry = False
        while not self.work.empty():   # requests that arrived while it was running
            item = self.work.get()
            if item is _STOP:
                raise _Quit()
            self.respond(item, error="The program was running.")
        self.refs = []
        self.paused = True
        self.event("stopped", {"reason": reason, "threadId": THREAD_ID, "allThreadsStopped": True})
        try:
            while True:
                request = self.work.get()
                if request is _STOP:
                    raise _Quit()
                command = request.get("command")
                if command in RESUME:
                    self.mode = RESUME[command]
                    self.mark_step(file)
                    self.paused = False
                    self.respond(request, {"allThreadsContinued": True})
                    return
                try:
                    self.respond(request, getattr(self, "on_" + command)(request.get("arguments") or {}))
                except _Quit:
                    raise
                except Exception as err:   # a bad request is answered, never a crash
                    self.respond(request, error=getattr(err, "message", None) or str(err))
        finally:
            self.paused = False

    # -- helpers --------------------------------------------------------------------------

    @staticmethod
    def key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def ref(self, value) -> int:
        """A variablesReference for a scope (a dict of names) or a list/map/object."""
        import tb

        if isinstance(value, _Scope) or isinstance(value, (list, dict)) and value or isinstance(value, tb.Instance) and value.fields:
            self.refs.append(value)
            return len(self.refs)
        return 0

    def variable(self, name: str, value) -> dict:
        import tb

        text = tb.to_text(value, nested=True)
        if len(text) > 1000:
            text = text[:997] + "..."
        return {"name": name, "value": text, "type": tb.type_name(value),
                "variablesReference": self.ref(value)}

    # -- requests handled on the reader thread ----------------------------------------------

    def on_initialize(self, args):
        return {"supportsConfigurationDoneRequest": True, "supportsEvaluateForHovers": True,
                "supportsTerminateRequest": True, "supportTerminateDebuggee": True,
                "supportsSetVariable": False, "supportsStepBack": False,
                "exceptionBreakpointFilters": []}

    def on_launch(self, args):
        program = args.get("program")
        if not program or not isinstance(program, str):
            raise ValueError('Tell me which file to debug: "program": "${file}" in launch.json.')
        cwd = args.get("cwd") or None
        if cwd and not os.path.isdir(cwd):
            raise ValueError(f"The folder '{cwd}' (\"cwd\" in launch.json) doesn't exist.")
        path = program if os.path.isabs(program) or not cwd else os.path.join(cwd, program)
        if not os.path.isfile(path):
            raise ValueError(f"I couldn't find the file '{program}'. Is the name spelled right?")
        self.path = os.path.abspath(path)
        self.cwd = cwd
        raw_args = args.get("args") or []
        self.args = [str(a) for a in raw_args] if isinstance(raw_args, list) else [str(raw_args)]
        if args.get("noDebug"):
            self.bps = {}
            self.no_debug = True
        if args.get("stopOnEntry"):
            self.mode, self.entry = "step", True
        self.launched = True
        if self.configured:
            self.ready.set()
        return None

    def on_attach(self, args):
        raise ValueError("TB can only launch a program to debug it (\"request\": \"launch\").")

    def on_setBreakpoints(self, args):
        source = args.get("source") or {}
        path = source.get("path")
        wanted = [b.get("line") for b in args.get("breakpoints") or []] or list(args.get("lines") or [])
        if not path:
            return {"breakpoints": [{"verified": False, "message": "TB can only stop in files."}
                                    for _ in wanted]}
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        result, good = [], set()
        for line in wanted:
            text = lines[line - 1].strip() if isinstance(line, int) and 0 < line <= len(lines) else ""
            if text and not text.startswith("#") and text not in ("}", "{"):
                good.add(line)
                result.append({"verified": True, "line": line})
            else:
                result.append({"verified": False, "line": line,
                               "message": "There's no code on this line to stop at."})
        if not self.no_debug:
            self.bps[self.key(path)] = good
        return {"breakpoints": result}

    def on_setExceptionBreakpoints(self, args):
        return {"breakpoints": []}

    def on_setFunctionBreakpoints(self, args):
        return {"breakpoints": [{"verified": False, "message": "Use a line breakpoint in TB."}
                                for _ in args.get("breakpoints") or []]}

    def on_configurationDone(self, args):
        self.configured = True
        if self.launched:
            self.ready.set()
        return None

    def on_threads(self, args):
        return {"threads": [{"id": THREAD_ID, "name": "main"}]}

    def on_pause(self, args):
        self.pause_asked = True
        self.mode = "step"
        return None

    def on_disconnect(self, args):
        self.quitting = True
        self.work.put(_STOP)
        self.ready.set()
        self.finished.set()
        return None

    on_terminate = on_disconnect

    # -- requests handled while the program is paused ------------------------------------------

    def on_stackTrace(self, args):
        frames = self.interp.frames() if self.interp else []
        result = []
        for k, frame in enumerate(reversed(frames)):
            file = frame.get("file") or self.path
            line = self.current[1] if k == 0 and self.current else frame["line"]
            name = frame["fn"] if frame["fn"] == "<main>" else f"{frame['fn']}()"
            result.append({"id": k, "name": name, "line": line or 1, "column": 1,
                           "source": {"name": os.path.basename(file), "path": os.path.abspath(file)}})
        start = args.get("startFrame") or 0
        levels = args.get("levels") or len(result)
        return {"stackFrames": result[start:start + levels], "totalFrames": len(result)}

    def on_scopes(self, args):
        frame_id = args.get("frameId") or 0
        if frame_id == 0:
            local_vars, global_vars = self.scopes()
        else:
            frames = list(reversed(self.interp.frames()))
            local_vars = frames[frame_id]["locals"] if frame_id < len(frames) else {}
            global_vars = self.scopes()[1]
        return {"scopes": [
            {"name": "Locals", "presentationHint": "locals", "variablesReference": self.ref(_Scope(local_vars)),
             "expensive": False},
            {"name": "Globals", "variablesReference": self.ref(_Scope(global_vars)), "expensive": False}]}

    def on_variables(self, args):
        import tb

        k = args.get("variablesReference", 0)
        if not 0 < k <= len(self.refs):
            return {"variables": []}
        value = self.refs[k - 1]
        out = []
        if isinstance(value, _Scope):
            for name in sorted(value):
                item = value[name]
                if name.startswith("__") or isinstance(item, (tb.Module, tb.Builtin)):
                    continue
                out.append(self.variable(name, item))
        elif isinstance(value, list):
            out = [self.variable(f"[{i}]", item) for i, item in enumerate(value[:1000])]
        elif isinstance(value, dict):
            out = [self.variable(tb.to_text(key, nested=True), item) for key, item in list(value.items())[:1000]]
        elif isinstance(value, tb.Instance):
            out = [self.variable(name, item) for name, item in value.fields.items()]
        return {"variables": out}

    def on_evaluate(self, args):
        import tb

        expression = (args.get("expression") or "").strip()
        if not expression:
            raise ValueError("What should I show?")
        try:
            value = self.interp.eval(expression, self.env)
        except tb.TBError as err:
            raise ValueError(err.message) from None
        result = self.variable(expression, value)
        return {"result": result["value"], "type": result["type"],
                "variablesReference": result["variablesReference"]}

    # -- running ------------------------------------------------------------------------------

    def reader_loop(self) -> None:
        try:
            while True:
                try:
                    request = self.read_message()
                except (ValueError, json.JSONDecodeError):
                    continue
                if request is None:
                    break
                if request.get("type") != "request":
                    continue
                command = request.get("command", "")
                if command in CONTROL:
                    try:
                        body = getattr(self, "on_" + command)(request.get("arguments") or {})
                    except Exception as err:
                        self.respond(request, error=str(err))
                    else:
                        self.respond(request, body)
                        if command == "initialize":
                            self.event("initialized")
                    if command in ("disconnect", "terminate"):
                        break
                elif not (command in RESUME or hasattr(self, "on_" + command)):
                    self.respond(request, error=f"TB's debugger doesn't support '{command}'.")
                elif self.paused:
                    self.work.put(request)
                else:
                    self.respond(request, error="The program isn't paused.")
        finally:
            # end of input: stop the program at its next line
            self.quitting = True
            self.work.put(_STOP)
            self.ready.set()
            self.finished.set()
            # a program busy in a library call never reaches its next line: don't outlive the editor
            timer = threading.Timer(3, os._exit, (0,))
            timer.daemon = True
            timer.start()

    def serve(self) -> int:
        real_out, real_err, real_in = sys.stdout, sys.stderr, sys.stdin
        threading.Thread(target=self.reader_loop, name="dap-reader", daemon=True).start()
        self.ready.wait()
        if self.quitting:
            return 0
        self.main_thread = threading.current_thread()
        sys.stdout, sys.stderr = _Events(self, "stdout"), _Events(self, "stderr")
        sys.stdin = io.StringIO("")
        old_cwd = os.getcwd()
        try:
            if self.cwd:
                os.chdir(self.cwd)
            code = self.run_program()
        finally:
            sys.stdout, sys.stderr, sys.stdin = real_out, real_err, real_in
            os.chdir(old_cwd)
        if code is not None and not self.quitting:
            self.event("exited", {"exitCode": code})
            self.event("terminated")
        self.finished.wait(timeout=30)
        return 0


def main(args: list[str] | None = None) -> int:
    return DapServer().serve()
