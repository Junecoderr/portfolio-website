"""Private helpers for libraries that call back into TB from other threads
(server, websocket, net.udp_listen, fs.watch).

The tree-walking interpreter keeps a little per-call state (call depth, the
current file), so TB callbacks from background threads take one shared lock:
only one callback runs at a time, and they never interleave with each other.
Inside parallel.loop() the loop's baton takes the place of that lock.
"""

from __future__ import annotations

import threading

import tb as _tb
from tbcore import tasks as _tasks

callback_lock = threading.RLock()


def guarded_call(api, fn, args, line=None):
    """Call a TB function from any thread. Returns (value, error) where error
    is the TBError that stopped it (or None)."""
    loop = _tasks.current_loop()
    with (_tasks.turn(loop) if loop is not None else callback_lock):
        try:
            return api.call(fn, list(args), line), None
        except _tb.TBError as err:
            return None, err
        except _tb.TBExit as stop:
            return None, _tb.TBError(f"The callback called exit({stop.code}).", line)
        except RecursionError:
            return None, _tb.TBError("The callback called itself too many times.", line)


def error_text(err) -> str:
    """The message of a TBError for logs and HTTP 500 bodies."""
    text = getattr(err, "message", None) or str(err)
    return text


def ignore_sigpipe() -> None:
    """Let network code survive a peer that hangs up mid-write.

    `tb` (tb.main) sets SIGPIPE to its default action so `tb prog.tb | head`
    stops quietly, but with that setting a browser closing a connection
    while the server is writing kills the whole program. Libraries that
    keep sockets open (server, websocket, tls, dns over TCP) call this
    first: writes to a closed socket then raise BrokenPipeError, which they
    handle. No-op on Windows and outside the main thread."""
    try:
        import signal

        if threading.current_thread() is threading.main_thread() and \
                signal.getsignal(signal.SIGPIPE) != signal.SIG_IGN:
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    except (AttributeError, ValueError, OSError):
        pass
