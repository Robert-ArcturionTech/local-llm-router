"""Run synthetic tests with connections limited to this process's fixture sockets."""
import errno
import socket
import sys
import threading
import unittest
from pathlib import Path

_lock = threading.RLock()
_bound = {}
_original_bind = socket.socket.bind
_original_close = socket.socket.close


def audit(event, args):
    if event not in ("socket.bind", "socket.connect"):
        return
    address = args[1]
    if not isinstance(address, tuple) or len(address) < 2 or address[0] != "127.0.0.1":
        raise ConnectionRefusedError(errno.ECONNREFUSED, "Synthetic CI denies non-loopback sockets")
    if event == "socket.bind":
        if address[1] != 0:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Synthetic CI requires an ephemeral fixture port")
    else:
        with _lock:
            if address[1] not in _bound.values():
                raise ConnectionRefusedError(errno.ECONNREFUSED, "Synthetic CI denies unowned listener port")


def fixture_bind(sock, address):
    _original_bind(sock, address)
    # The audit hook admits only 127.0.0.1:0; record the OS-assigned port.
    with _lock:
        _bound[sock] = sock.getsockname()[1]


def fixture_close(sock):
    with _lock:
        _bound.pop(sock, None)
    return _original_close(sock)


socket.socket.bind = fixture_bind
socket.socket.close = fixture_close
sys.addaudithook(audit)

# Exercise the installed audit hook without connecting or contacting a service.
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fixture:
    fixture.bind(("127.0.0.1", 0))
    port = fixture.getsockname()[1]
    sys.audit("socket.connect", None, ("127.0.0.1", port))
    unrelated_port = port + 1 if port < 65535 else port - 1
    for address in (("127.0.0.1", 1), ("127.0.0.1", unrelated_port), ("192.0.2.1", 443)):
        try:
            sys.audit("socket.connect", None, address)
        except ConnectionRefusedError:
            pass
        else:
            raise RuntimeError("Synthetic CI allowed an unowned address")
try:
    sys.audit("socket.connect", None, ("127.0.0.1", port))
except ConnectionRefusedError:
    pass
else:
    raise RuntimeError("Closed fixture port remained registered")

sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / "tests")]
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
