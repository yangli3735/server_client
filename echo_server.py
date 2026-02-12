#!/usr/bin/env python3
# Echo server supporting multiple concurrent clients using epoll (Linux only)

import socket
import select
import errno

ECHO_PORT = 9999
BUF_SIZE = 4096

READ_FLAGS = select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR
READ_WRITE_FLAGS = select.EPOLLIN | select.EPOLLOUT | select.EPOLLHUP | select.EPOLLERR


def safe_unregister(ep, fd):
    try:
        ep.unregister(fd)
    except Exception:
        pass


def cleanup(ep, conns, outbuf, fd):
    sock = conns.pop(fd, None)
    outbuf.pop(fd, None)
    if ep is not None:
        safe_unregister(ep, fd)
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass


def try_flush(ep, conns, outbuf, fd):
    """Try to send as much as possible from outbuf[fd] without blocking."""
    sock = conns.get(fd)
    if sock is None:
        return

    buf = outbuf.get(fd)
    if not buf:
        # nothing pending; only need read notifications
        try:
            ep.modify(fd, READ_FLAGS)
        except Exception:
            pass
        return

    while buf:
        try:
            sent = sock.send(buf)
            if sent == 0:
                cleanup(ep, conns, outbuf, fd)
                return
            del buf[:sent]
        except BlockingIOError:
            break
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            cleanup(ep, conns, outbuf, fd)
            return

    # If still pending, listen for EPOLLOUT; otherwise, back to read-only.
    if fd in conns:
        try:
            ep.modify(fd, READ_WRITE_FLAGS if outbuf[fd] else READ_FLAGS)
        except Exception:
            pass


def main():
    print("----- Echo Server (epoll) -----")

    serverSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serverSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serverSock.bind(("0.0.0.0", ECHO_PORT))
    serverSock.listen(256)
    serverSock.setblocking(False)

    ep = select.epoll()
    ep.register(serverSock.fileno(), select.EPOLLIN)

    conns = {}          # fd -> socket
    outbuf = {}         # fd -> bytearray

    try:
        while True:
            # Short poll interval helps reduce latency under stress
            events = ep.poll(0.05)

            for fd, ev in events:
                if fd == serverSock.fileno():
                    # accept all ready connections
                    while True:
                        try:
                            conn, _ = serverSock.accept()
                            conn.setblocking(False)
                            cfd = conn.fileno()
                            conns[cfd] = conn
                            outbuf[cfd] = bytearray()
                            ep.register(cfd, READ_FLAGS)
                        except BlockingIOError:
                            break
                        except OSError as e:
                            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                                break
                            raise
                    continue

                sock = conns.get(fd)
                if sock is None:
                    continue

                if ev & (select.EPOLLHUP | select.EPOLLERR):
                    cleanup(ep, conns, outbuf, fd)
                    continue

                if ev & select.EPOLLIN:
                    # read all available data
                    while True:
                        try:
                            data = sock.recv(BUF_SIZE)
                            if not data:
                                cleanup(ep, conns, outbuf, fd)
                                break
                            outbuf[fd].extend(data)
                        except BlockingIOError:
                            break
                        except OSError as e:
                            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                                break
                            cleanup(ep, conns, outbuf, fd)
                            break

                    # immediately try to send back
                    if fd in conns:
                        try_flush(ep, conns, outbuf, fd)

                if ev & select.EPOLLOUT:
                    # socket writable; flush pending
                    try_flush(ep, conns, outbuf, fd)

    finally:
        try:
            safe_unregister(ep, serverSock.fileno())
        except Exception:
            pass
        ep.close()
        for fd in list(conns.keys()):
            cleanup(None, conns, outbuf, fd)
        serverSock.close()

