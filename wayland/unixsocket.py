# Copyright (c) 2024 Graham R King
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice (including the
# next paragraph) shall be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

import array
import errno
import socket
import struct
import threading
from collections import deque

from wayland.constants import PROTOCOL_HEADER_SIZE


class UnixSocketConnection(threading.Thread):
    READ_BUFFER_SIZE = 4096

    def __init__(self, socket_path: str, buffer_size: int = 2**18):
        super().__init__(daemon=True)
        self.socket_path = socket_path
        self.buffer = deque(maxlen=buffer_size)
        self.fd_buffer = deque(maxlen=buffer_size)
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(self.socket_path)
        self.stop_event = threading.Event()
        self.read_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.buffer_lock = threading.Lock()
        self.fd_buffer_lock = threading.Lock()
        self.start()

    def _read(self) -> tuple[bytes, int | None]:
        peek = self._socket.recv(PROTOCOL_HEADER_SIZE, socket.MSG_PEEK)
        _, _, message_size = struct.unpack_from("IHH", peek)
        fdsize = array.array("i").itemsize
        data, ancdata, _, _ = self._socket.recvmsg(
            message_size, socket.CMSG_LEN(fdsize)
        )

        fd = next(
            (
                struct.unpack("I", cmsg_data)[-1]
                for cmsg_level, cmsg_type, cmsg_data in ancdata
                if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS
            ),
            None,
        )

        return data, fd

    def read(self) -> None:
        with self.read_lock:
            data, fd = self._read()

        with self.buffer_lock:
            self.buffer.append(data)

        if fd is not None:
            with self.fd_buffer_lock:
                self.fd_buffer.append(fd)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.read()
            except OSError as e:
                if e.errno not in {errno.EWOULDBLOCK, errno.EAGAIN}:
                    break
            except Exception:
                break

    def stop(self) -> None:
        self.stop_event.set()
        self.join()

    def sendmsg(self, buffers: list[bytes], ancillary: list[tuple]) -> None:
        with self.write_lock:
            self._socket.sendmsg(buffers, ancillary)

    def sendall(self, data: bytes) -> None:
        with self.write_lock:
            self._socket.sendall(data)

    def get_next_message(self) -> bytes | None:
        with self.buffer_lock:
            return self.buffer.popleft() if self.buffer else None

    def get_next_fd(self) -> int | None:
        with self.fd_buffer_lock:
            return self.fd_buffer.popleft() if self.fd_buffer else None
