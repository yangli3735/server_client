import socket
import threading

ECHO_PORT = localhost 
BUF_SIZE =  4096 

def handle_client(conn: socket.socket, addr):
    try:
        # 可选：避免连接长期卡死
        conn.settimeout(10.0)
        while True:
            data = conn.recv(BUF_SIZE)
            if not data:
                break
            # sendall 保证把 data 全部发出去
            conn.sendall(data)
    except (ConnectionResetError, ConnectionAbortedError, socket.timeout):
        # 客户端异常断开或超时，直接结束该连接
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

def main():
    print("----- Echo Server -----")

    serverSock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serverSock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serverSock.bind(("0.0.0.0", ECHO_PORT))
    # backlog 要足够大，避免并发 connect 被拒
    serverSock.listen(256)

    while True:
        conn, addr = serverSock.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()

if __name__ == "__main__":
    main()
