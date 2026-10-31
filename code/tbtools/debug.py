"""`tb debug FILE.tb [args...]`: step through a TB program line by line.

Commands (type them at the `(tb)` prompt; short forms in brackets):

    break LINE | FILE:LINE  [b]   stop when that line is about to run
    delete [LINE|all]       [d]   remove breakpoints
    breaks                        list breakpoints
    run                     [r]   start the program (again)
    step                    [s]   run one line, going into function calls
    next                    [n]   run one line, stepping over function calls
    out                     [o]   run until the current function returns
    continue                [c]   run until the next breakpoint
    print EXPR              [p]   show the value of an expression (or run a statement)
    locals                  [l]   the current function's parameters and variables
    globals                 [g]   the module-level names and their values
    backtrace               [bt]  the functions that are running, innermost last
    list                          the source around the current line
    help                    [h]   this list
    quit                    [q]   stop debugging

It is built on the interpreter's trace hook (`Interpreter.trace`,
`frames()`, `eval()`), so it sees exactly what the program does. Limits:
code inside a generator runs on its own thread and is not stepped into; a
program that reads its input with ask() shares the keyboard with the
debugger prompt.
"""

from __future__ import annotations

import os
import sys
import threading

__all__ = ["main", "Debugger"]

HELP = __doc__.split("Commands", 1)[1].split("It is built", 1)[0]


class _Quit(BaseException):
    """Leave the program right away (not catchable by TB code)."""


class Debugger:
    def __init__(self, path: str, args: list[str], stdin=None, stdout=None):
        self.path = os.path.abspath(path)
        self.args = args
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.breakpoints: list[tuple[str | None, int]] = []
        self.mode = "continue"     # continue | step | next | out
        self.depth = 0             # our own count of active calls
        self.target_depth = 0
        self.interp = None
        self.sources: dict[str, list[str]] = {}
        self.current: tuple[str | None, int] | None = None
        self.env = None
        self.running = False
        self.main_thread = threading.main_thread()

    # -- output and input ------------------------------------------------------

    def say(self, text: str = "") -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()

    def read_command(self) -> str | None:
        self.stdout.write("(tb) ")
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:
            self.say()
            return None
        return line.strip()

    def source_lines(self, file: str | None) -> list[str]:
        file = file or self.path
        if file not in self.sources:
            try:
                with open(file, encoding="utf-8") as f:
                    self.sources[file] = f.read().splitlines()
            except OSError:
                self.sources[file] = []
        return self.sources[file]

    def shown(self, file: str | None) -> str:
        file = file or self.path
        try:
            rel = os.path.relpath(file)
        except ValueError:
            return file
        return file if rel.startswith("..") else rel

    def show_line(self, file: str | None, line: int) -> None:
        lines = self.source_lines(file)
        text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        self.say(f"-> {self.shown(file)}:{line}   {text}")

    # -- breakpoints ---------------------------------------------------------------

    def parse_location(self, text: str) -> tuple[str | None, int] | None:
        file = None
        spot = text.strip()
        if ":" in spot and not spot.endswith(":"):
            file, _, spot = spot.rpartition(":")
        try:
            line = int(spot)
        except ValueError:
            return None
        if line < 1:
            return None
        if file is not None:
            candidate = file if os.path.isabs(file) else os.path.join(os.path.dirname(self.path), file)
            if not os.path.exists(candidate) and os.path.exists(file):
                candidate = file
            file = os.path.abspath(candidate)
        return (file, line)

    def at_breakpoint(self, file: str | None, line: int) -> bool:
        here = os.path.abspath(file) if file else self.path
        for bp_file, bp_line in self.breakpoints:
            if bp_line != line:
                continue
            if bp_file is None:
                if here == self.path:
                    return True
            elif bp_file == here:
                return True
        return False

    # -- the trace hook -----------------------------------------------------------------

    def hook(self, event, line, file, env):
        if threading.current_thread() is not self.main_thread:
            return   # generator bodies run on helper threads: not stepped
        if event == "call":
            self.depth += 1
            return
        if event in ("return", "exception"):
            self.depth = max(0, self.depth - 1)
            return
        if event != "line":
            return
        stop = False
        if self.at_breakpoint(file, line):
            stop = True
        elif self.mode == "step":
            stop = True
        elif self.mode == "next":
            stop = self.depth <= self.target_depth
        elif self.mode == "out":
            stop = self.depth < self.target_depth
        if not stop:
            return
        self.current = (file, line)
        self.env = env
        self.show_line(file, line)
        self.command_loop(running=True)

    # -- commands -----------------------------------------------------------------------

    def command_loop(self, running: bool) -> str:
        """Read commands until one resumes the program. Returns the resume
        command ("run" when not running)."""
        while True:
            command = self.read_command()
            if command is None:
                raise _Quit()
            if not command:
                continue
            word, _, rest = command.partition(" ")
            word = word.lower()
            rest = rest.strip()
            if word in ("q", "quit", "exit"):
                raise _Quit()
            if word in ("h", "help", "?"):
                self.say(HELP.rstrip())
            elif word in ("b", "break"):
                self.cmd_break(rest)
            elif word in ("d", "delete", "clear"):
                self.cmd_delete(rest)
            elif word in ("breaks", "breakpoints", "info"):
                self.cmd_breaks()
            elif word in ("list", "ls"):
                self.cmd_list(rest)
            elif word in ("r", "run"):
                if running:
                    self.say("The program is already running. Use 'continue', or 'quit' and start again.")
                    continue
                self.mode = "continue"
                return "run"
            elif word in ("s", "step"):
                if not running:
                    self.mode = "step"
                    return "run"
                self.mode = "step"
                return "step"
            elif word in ("n", "next"):
                if not running:
                    self.mode = "step"
                    return "run"
                self.mode = "next"
                self.target_depth = self.depth
                return "next"
            elif word in ("o", "out", "finish"):
                if not running:
                    self.say("The program isn't running yet. Type 'run' or 'step'.")
                    continue
                self.mode = "out"
                self.target_depth = self.depth
                return "out"
            elif word in ("c", "continue", "cont"):
                if not running:
                    self.mode = "continue"
                    return "run"
                self.mode = "continue"
                return "continue"
            elif word in ("p", "print", "eval"):
                self.cmd_print(rest, running)
            elif word in ("l", "locals"):
                self.cmd_locals(running)
            elif word in ("g", "globals"):
                self.cmd_globals(running)
            elif word in ("bt", "backtrace", "where", "stack"):
                self.cmd_backtrace(running)
            else:
                self.say(f"I don't know the command '{word}'. Type 'help' to see them all.")

    def cmd_break(self, rest: str) -> None:
        if not rest:
            self.say("Which line? Like: break 12  (or break helpers.tb:5)")
            return
        spot = self.parse_location(rest)
        if spot is None:
            self.say(f"'{rest}' isn't a line number. Like: break 12  (or break helpers.tb:5)")
            return
        file, line = spot
        lines = self.source_lines(file)
        if lines and line > len(lines):
            self.say(f"{self.shown(file)} has only {len(lines)} lines.")
            return
        if spot not in self.breakpoints:
            self.breakpoints.append(spot)
        text = lines[line - 1].strip() if lines and line <= len(lines) else ""
        self.say(f"Breakpoint at {self.shown(file)}:{line}" + (f"   {text}" if text else ""))

    def cmd_delete(self, rest: str) -> None:
        if not rest or rest == "all":
            count = len(self.breakpoints)
            self.breakpoints.clear()
            self.say(f"Removed {count} breakpoint{'' if count == 1 else 's'}.")
            return
        spot = self.parse_location(rest)
        if spot in self.breakpoints:
            self.breakpoints.remove(spot)
            self.say(f"Removed the breakpoint at {self.shown(spot[0])}:{spot[1]}.")
        else:
            self.say(f"There's no breakpoint at {rest}.")

    def cmd_breaks(self) -> None:
        if not self.breakpoints:
            self.say("No breakpoints. Add one with: break LINE")
        for file, line in self.breakpoints:
            self.say(f"  {self.shown(file)}:{line}")

    def cmd_list(self, rest: str) -> None:
        file, line = self.current if self.current else (None, 1)
        if rest:
            try:
                line = int(rest)
            except ValueError:
                self.say("Like: list  or  list 20")
                return
        lines = self.source_lines(file)
        start = max(1, line - 4)
        for n in range(start, min(len(lines), line + 4) + 1):
            mark = "->" if self.current and n == self.current[1] and file == self.current[0] else "  "
            self.say(f"{mark} {n:4}  {lines[n - 1]}")

    def cmd_print(self, rest: str, running: bool) -> None:
        if not rest:
            self.say("What should I show? Like: print total  or  print items[0]")
            return
        if not running or self.interp is None:
            self.say("The program isn't running yet. Type 'run' or 'step' first.")
            return
        import tb

        try:
            value = self.interp.eval(rest, self.env)
        except tb.TBError as err:
            self.say(f"Oops! {err.message}")
            return
        except _Quit:
            raise
        except Exception as err:  # never a Python traceback
            self.say(f"Oops! That didn't work: {err}")
            return
        self.say(tb.to_text(value, nested=True))

    def scopes(self) -> tuple[dict, dict]:
        """(locals, globals) at the current stop. The env chain ends in the
        module's globals, whose parent is the builtins (the root, no parent)."""
        chain = []
        env = self.env
        while env is not None and env.parent is not None:
            chain.append(env)
            env = env.parent
        if not chain:
            return {}, {}
        frames = self.interp.frames()
        in_function = bool(frames) and frames[-1]["fn"] != "<main>"
        # at the top level the "current function" is the module itself
        local_vars: dict = {}
        if in_function and getattr(self.env, "frame", None) is not None:
            # VM: one FrameEnv per call; leave out what a closure captured from outside
            code = self.env.frame.code
            outer = {name for name, _ in code.free_names} - {entry[0] for entry in code.varinfo}
            local_vars = {k: v for k, v in chain[0].vars.items() if k not in outer}
        elif in_function:
            # tree: block scopes up to the call's own scope, not the enclosing function's
            call_scope = self.interp.call_stack[-1].env
            for scope in chain[:-1]:
                for k, v in scope.vars.items():
                    local_vars.setdefault(k, v)
                if scope is call_scope:
                    break
        else:
            for scope in chain:
                for k, v in scope.vars.items():
                    local_vars.setdefault(k, v)
        return local_vars, dict(chain[-1].vars)

    def cmd_locals(self, running: bool) -> None:
        if not running or self.interp is None:
            self.say("The program isn't running yet. Type 'run' or 'step' first.")
            return
        self.show_names(self.scopes()[0])

    def cmd_globals(self, running: bool) -> None:
        if not running or self.interp is None:
            self.say("The program isn't running yet. Type 'run' or 'step' first.")
            return
        self.show_names(self.scopes()[1])

    def show_names(self, names: dict) -> None:
        import tb

        shown = 0
        for name in sorted(names):
            value = names[name]
            if isinstance(value, (tb.Module, tb.Builtin)) or name.startswith("__"):
                continue
            text = tb.to_text(value, nested=True)
            if len(text) > 200:
                text = text[:197] + "..."
            self.say(f"  {name} = {text}")
            shown += 1
        if not shown:
            self.say("  (no names yet)")

    def cmd_backtrace(self, running: bool) -> None:
        if not running or self.interp is None:
            self.say("The program isn't running yet.")
            return
        frames = self.interp.frames()
        for k, frame in enumerate(frames):
            line = frame["line"]
            if k == len(frames) - 1 and self.current:
                line = self.current[1]
            name = frame["fn"]
            label = name if name == "<main>" else f"{name}()"
            self.say(f"  {label} at {self.shown(frame['file'])}:{line}")

    # -- running ----------------------------------------------------------------------------

    def run_program(self) -> int | None:
        """Run the program once. Returns its exit code, or None after 'quit'."""
        import tb

        try:
            with open(self.path, encoding="utf-8") as f:
                source = f.read()
        except OSError as err:
            self.say(f"Oops! I couldn't open '{self.path}': {err.strerror or err}.")
            return 1
        self.interp = tb.Interpreter()
        self.interp.trace = self.hook
        self.depth = 0
        self.current = None
        self.sources.pop(self.path, None)
        code = 0
        try:
            tb.run(source, interpreter=self.interp, path=self.path, args=list(self.args))
            self.say("The program finished.")
        except _Quit:
            return None
        except tb.TBError as err:
            self.say(str(err))
            trace = err.trace_text() if hasattr(err, "trace_text") else ""
            if trace:
                self.say(trace)
            self.say("The program stopped because of the error above.")
            code = 1
        except tb.TBExit as stop:
            code = stop.code if isinstance(stop.code, int) else 0
            self.say(f"The program called exit({code}).")
        except KeyboardInterrupt:
            self.say("Stopped.")
            code = 130
        finally:
            self.interp.trace = None
        return code

    def start(self) -> int:
        self.say(f"Debugging {self.shown(self.path)}. Type 'help' for the commands, 'run' to start.")
        code = 0
        try:
            while True:
                self.command_loop(running=False)
                result = self.run_program()
                if result is None:
                    return code
                code = result
                self.say("Type 'run' to start again, or 'quit'.")
        except _Quit:
            return code


def main(args: list[str] | None = None) -> int:
    args = list(args or [])
    if not args or args[0] in ("-h", "--help"):
        print("tb debug FILE.tb [args...] - step through a program line by line")
        print("Commands" + HELP.rstrip())
        return 0 if args else 2
    path = args[0]
    program_args = args[1:]
    if program_args and program_args[0] == "--":
        program_args = program_args[1:]
    if not os.path.isfile(path):
        print(f"Oops! I couldn't find the file '{path}'. Is the name spelled right?", file=sys.stderr)
        return 1
    return Debugger(path, program_args).start()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
