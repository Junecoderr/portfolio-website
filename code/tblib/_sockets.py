"""Small socket helpers shared by the libraries that listen on a port.

Listening sockets must refuse a port another program already holds, on
every OS. On Linux and macOS that is the default as long as SO_REUSEADDR is
not set on UDP sockets (for TCP it only lets a server restart while old
connections linger in TIME_WAIT). On Windows SO_REUSEADDR lets a second
socket steal a busy port, so listeners use SO_EXCLUSIVEADDRUSE instead, and
Windows reports a busy port as WSAEADDRINUSE (10048) or, when the other
socket holds it exclusively, WSAEACCES (10013)."""

from __future__ import annotations

import errno
import os
import socket

WINDOWS = os.name == "nt"

_IN_USE = {errno.EADDRINUSE, 48, 98, 10048}
_NO_ADDRESS = {errno.EADDRNOTAVAIL, 49, 99, 10049}
_WSAEACCES = 10013


def exclusive(sock: socket.socket) -> None:
    """On Windows, ask for the port exclusively so nobody can share it."""
    option = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if WINDOWS and option is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, option, 1)
        except OSError:
            pass


def _codes(err: OSError) -> set:
    return {getattr(err, "errno", None), getattr(err, "winerror", None)}


def port_in_use(err: OSError, port: int) -> bool:
    """Did binding fail because something else is already using the port?"""
    codes = _codes(err)
    if codes & _IN_USE or "in use" in str(err).lower():
        return True
    # Windows says "access denied" for a port another socket holds
    # exclusively (and for ports Windows reserves). It never needs
    # administrator rights for a port, so for ordinary ports this means busy.
    return WINDOWS and port >= 1024 and bool(codes & {_WSAEACCES, errno.EACCES})


def no_such_address(err: OSError) -> bool:
    return bool(_codes(err) & _NO_ADDRESS) or "assign" in str(err).lower()
