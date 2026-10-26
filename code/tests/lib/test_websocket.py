"""websocket library (client) tested against the server library's app.ws(),
plus raw-socket checks of the RFC 6455 framing."""

import socket
import struct
import threading

import pytest

import tb
import tblib.server as serverlib
import tblib.websocket as wslib
from conftest import run_tb, tb_error


@pytest.fixture(autouse=True)
def stop_servers():
    yield
    serverlib.stop_all()


ECHO_APP = '''
import server
import websocket
let app = server.create({"log": false})
app.ws("/echo/{room}", fn(ws, req) {
    ws.send("welcome to " + req.params.room)
    ws.each(fn(msg) {
        if msg == "bye" { return false }
        ws.send("echo: " + msg)
    })
})
let url = app.start(0)
let wsurl = "ws" + url.slice(4)
'''


def test_echo_round_trip_against_app_ws():
    out = run_tb(ECHO_APP + '''
let ws = websocket.connect(wsurl + "/echo/lobby")
say(ws.receive(5))
ws.send("hello")
say(ws.receive(5))
ws.send({"user": "Tanisha"})
say(ws.receive(5))
say(ws.is_open())
ws.close()
say(ws.open)
app.stop()
''')
    assert out == ["welcome to lobby", "echo: hello", 'echo: {"user": "Tanisha"}', "true", "false"]


def test_server_hangup_gives_closed_error():
    out = run_tb(ECHO_APP + '''
let ws = websocket.connect(wsurl + "/echo/x")
ws.receive(5)
ws.send("bye")
try {
    ws.receive(5)
    say("no error")
} catch err {
    say(err.reason)
}
say(ws.open)
app.stop()
''')
    assert out == ["closed", "false"]


def test_receive_timeout_gives_nil_and_each_stops():
    out = run_tb('''
import server
import websocket
let app = server.create({"log": false})
app.ws("/quiet", fn(ws, req) { ws.receive(2) })
let url = app.start(0)
let ws = websocket.connect("ws" + url.slice(4) + "/quiet")
say(ws.receive(0.2))
let seen = []
ws.each(fn(m) { seen.push(m) }, 0.2)
say(len(seen))
ws.close()
app.stop()
''')
    assert out == ["nil", "0"]


def test_plain_http_to_ws_path_is_426_and_http_server_refuses_upgrade():
    out = run_tb('''
import server
import http
import websocket
let app = server.create({"log": false})
app.ws("/live", fn(ws, req) { ws.close() })
app.get("/page", fn(req) { return "page" })
let url = app.start(0)
say(http.get(url + "/live").status)
try {
    websocket.connect("ws" + url.slice(4) + "/page")
} catch err {
    say(err.reason)
}
app.stop()
''')
    assert out == ["426", "handshake"]


def test_friendly_connect_errors():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    err = tb_error(f'import websocket\nwebsocket.connect("ws://127.0.0.1:{port}/")')
    assert "Nothing is listening" in err.message
    assert "ws:// or wss://" in tb_error('import websocket\nwebsocket.connect("http://example.com")').message
    assert "doesn't know the option" in tb_error('import websocket\nwebsocket.connect("ws://x", {"nope": 1})').message
    assert "line breaks" in tb_error(
        'import websocket\nwebsocket.connect("ws://127.0.0.1:1", {"headers": {"A": "b\\r\\nc"}})').message


def test_is_url():
    assert run_tb('import websocket\nsay(websocket.is_url("wss://a.example/x"))\nsay(websocket.is_url("http://a"))') \
        == ["true", "false"]


# -- framing --------------------------------------------------------------------


def test_frame_encode_decode_round_trip_all_lengths():
    for size in (0, 5, 125, 126, 300, 65535, 65536, 70000):
        payload = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
        for mask in (False, True):
            a, b = socket.socketpair()
            try:
                frame = wslib.encode_frame(wslib.OP_BINARY, payload, mask)
                sender = threading.Thread(target=a.sendall, args=(frame,))
                sender.start()
                fin, op, data = wslib.read_frame(b)
                sender.join(5)
                assert fin and op == wslib.OP_BINARY and data == payload
            finally:
                a.close()
                b.close()


def test_client_frames_are_masked_and_server_frames_are_not():
    raw = wslib.encode_frame(wslib.OP_TEXT, b"hi", True)
    assert raw[1] & 0x80 and len(raw) == 2 + 4 + 2
    raw = wslib.encode_frame(wslib.OP_TEXT, b"hi", False)
    assert not raw[1] & 0x80 and raw[2:] == b"hi"


def test_ping_is_answered_and_fragments_are_joined():
    a, b = socket.socketpair()
    try:
        conn = wslib.Connection(b, mask=True)
        a.sendall(wslib.encode_frame(wslib.OP_PING, b"p", False))
        a.sendall(bytes([wslib.OP_TEXT, 3]) + b"hel")            # fin=0, text
        a.sendall(bytes([0x80 | wslib.OP_CONT, 2]) + b"lo")      # fin=1, continuation
        assert conn.receive(2) == "hello"
        a.settimeout(2)
        fin, op, data = wslib.read_frame(a)                       # the pong (masked, unmasked by reader)
        assert op == wslib.OP_PONG and data == b"p"
    finally:
        a.close()
        b.close()


def test_oversized_frame_is_rejected():
    a, b = socket.socketpair()
    try:
        conn = wslib.Connection(b, mask=True)
        a.sendall(bytes([0x80 | wslib.OP_BINARY, 127]) + struct.pack("!Q", wslib.MAX_MESSAGE + 1))
        with pytest.raises(wslib.WsProblem) as info:
            conn.receive(2)
        assert info.value.reason == "too_big"
    finally:
        a.close()
        b.close()


def test_documented():
    import json
    text = json.dumps(tb.describe_library("websocket"), default=str)
    assert "connect" in text and "is_url" in text
