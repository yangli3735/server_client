#!/usr/bin/env python3
# Echo server supporting multiple concurrent clients using epoll (Linux only)

import socket
import select
import errno

ECHO_PORT = 9999
BUF_SIZE = 4096


def main():
    print("----- Echo Server (epoll) -----")

    # 1) Create listening socket
    serverSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serverSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serverSock.bind(("0.0.0.0", ECHO_PORT))
    serverSock.listen(128)
    serverSock.setblocking(False)

    # 2) Create epoll object and register the listening socket for read events
    ep = select.epoll()
    ep.register(serverSock.fileno(), select.EPOLLIN)

    # fd -> socket
    conns = {}
    # fd -> bytearray (pending data to send)
    outbuf = {}

    try:
        while True:
            events = ep.poll(1)  # timeout=1s; adjust as needed
            for fd, ev in events:
                if fd == serverSock.fileno():
                    # Accept as many connections as are ready
                    while True:
                        try:
                            conn, addr = serverSock.accept()
                            conn.setblocking(False)
                            cfd = conn.fileno()
                            conns[cfd] = conn
                            outbuf[cfd] = bytearray()
                            # Start by listening for read; we'll add write when needed
                            ep.register(cfd, select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR)
                        except BlockingIOError:
                            break
                        except OSError as e:
                            # If accept fails for transient reasons, stop accepting this round
                            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                                break
                            raise

                else:
                    conn = conns.get(fd)
                    if conn is None:
                        continue

                    # Handle hangup/error first
                    if ev & (select.EPOLLHUP | select.EPOLLERR):
                        cleanup(ep, conns, outbuf, fd)
                        continue

                    # Read ready
                    if ev & select.EPOLLIN:
                        while True:
                            try:
                                data = conn.recv(BUF_SIZE)
                                if not data:
                                    # Client closed
                                    cleanup(ep, conns, outbuf, fd)
                                    break
                                # Echo: queue data to send back
                                outbuf[fd].extend(data)
                            except BlockingIOError:
                                break
                            except OSError as e:
                                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                                    break
                                cleanup(ep, conns, outbuf, fd)
                                break

                        # If we have something to send, enable EPOLLOUT
                        if fd in conns and outbuf.get(fd):
                            ep.modify(fd, select.EPOLLIN | select.EPOLLOUT | select.EPOLLHUP | select.EPOLLERR)

                    # Write ready
                    if ev & select.EPOLLOUT:
                        buf = outbuf.get(fd)
                        if not buf:
                            # Nothing left to send; stop listening for write
                            if fd in conns:
                                ep.modify(fd, select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR)
                            continue

                        while buf:
                            try:
                                sent = conn.send(buf)
                                if sent == 0:
                                    cleanup(ep, conns, outbuf, fd)
                                    break
                                del buf[:sent]
                            except BlockingIOError:
                                break
                            except OSError as e:
                                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                                    break
                                cleanup(ep, conns, outbuf, fd)
                                break

                        # If fully sent, stop listening for write
                        if fd in conns and not outbuf[fd]:
                            ep.modify(fd, select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR)

    finally:
        # Cleanup all
        try:
            ep.unregister(serverSock.fileno())
        except Exception:
            pass
        ep.close()
        for fd in list(conns.keys()):
            cleanup(None, conns, outbuf, fd)
        serverSock.close()


def cleanup(ep, conns, outbuf, fd):
    sock = conns.pop(fd, None)
    outbuf.pop(fd, None)
    if ep is not None:
        try:
            ep.unregister(fd)
        except Exception:
            pass
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
