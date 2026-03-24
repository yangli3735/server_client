import socket
import threading
import time
from grading import MSS, DEFAULT_TIMEOUT
from collections import defaultdict

# Constants for simplified TCP
SYN_FLAG = 0x01
ACK_FLAG = 0x02
FIN_FLAG = 0x04
SACK_FLAG = 0x08

EXIT_SUCCESS = 0
EXIT_ERROR = 1

class ReadMode:
    NO_FLAG = 0
    NO_WAIT = 1
    TIMEOUT = 2

class Packet:
    def __init__(self, seq=0, ack=0, flags=0, payload=b"", sack_blocks=None):
        self.seq = seq
        self.ack = ack
        self.flags = flags
        self.payload = payload
        self.sack_blocks = sack_blocks or []

    def encode(self):
        header = f"{self.seq},{self.ack},{self.flags},{len(self.payload)}"
        sack_info = ";".join(f"{start}-{end}" for start, end in self.sack_blocks)
        header_with_sack = f"{header},{sack_info}|".encode()
        return header_with_sack + self.payload

    @staticmethod
    def decode(data):
        header_end = data.find(b"|")
        header_part = data[:header_end].decode()
        payload = data[header_end + 1:]
        
        parts = header_part.split(",")
        seq, ack, flags, data_len = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        sack_info = parts[4] if len(parts) > 4 and parts[4] else ""
        sack_blocks = []
        if sack_info:
            for block in sack_info.split(";"):
                if block and "-" in block:
                    start, end = block.split("-")
                    sack_blocks.append((int(start), int(end)))
        return Packet(seq, ack, flags, payload, sack_blocks)


class TransportSocket:
    def __init__(self):
        self.sock_fd = None
        self.recv_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.wait_cond = threading.Condition(self.recv_lock)

        self.death_lock = threading.Lock()
        self.dying = False
        self.thread = None

        self.window = {
            "last_ack": 0,
            "next_seq_expected": 0,
            "recv_buf": b"",
            "recv_len": 0,
            "next_seq_to_send": 0,
        }
        self.sock_type = None
        self.conn = None
        self.my_port = None
        self.state = "CLOSED"
        self.seq_num = 0
        self.ack_num = 0
        
        # SACK and retransmission tracking
        self.unacked_segments = {}  # {seq: Packet} - tracks unacknowledged segments
        self.duplicate_acks = defaultdict(int)  # {ack_num: count}
        self.out_of_order_buffer = {}  # {seq: payload} - buffer for out-of-order packets
        self.last_ack_received = 0  # Track last ACK for duplicate detection

    def socket(self, sock_type, port, server_ip=None):
        self.sock_fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_type = sock_type

        if sock_type == "TCP_INITIATOR":
            self.conn = (server_ip, port)
            self.sock_fd.bind(("", 0))
        elif sock_type == "TCP_LISTENER":
            self.sock_fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock_fd.bind(("", port))
        else:
            print("Unknown socket type")
            return EXIT_ERROR

        self.sock_fd.settimeout(1.0)
        self.my_port = self.sock_fd.getsockname()[1]

        self.thread = threading.Thread(target=self.backend, daemon=True)
        self.thread.start()
        return EXIT_SUCCESS

    def close(self):
        self.death_lock.acquire()
        try:
            self.dying = True
        finally:
            self.death_lock.release()

        if self.thread:
            self.thread.join()

        if self.sock_fd:
            self.sock_fd.close()
        else:
            print("Error: Null socket")
            return EXIT_ERROR

        return EXIT_SUCCESS

    def send(self, data):
        if not self.conn:
            raise ValueError("Connection not established.")
        with self.send_lock:
            self.send_segment(data)

    def recv(self, buf, length, flags):
        """
        Retrieve received data from the buffer, with optional blocking behavior.
        """
        read_len = 0

        if length < 0:
            print("ERROR: Negative length")
            return EXIT_ERROR

        if flags == ReadMode.NO_FLAG:
            with self.wait_cond:
                while self.window["recv_len"] == 0:
                    self.wait_cond.wait()

        self.recv_lock.acquire()
        try:
            if flags in [ReadMode.NO_WAIT, ReadMode.NO_FLAG]:
                if self.window["recv_len"] > 0:
                    read_len = min(self.window["recv_len"], length)
                    buf[0] = self.window["recv_buf"][:read_len]

                    if read_len < self.window["recv_len"]:
                        self.window["recv_buf"] = self.window["recv_buf"][read_len:]
                        self.window["recv_len"] -= read_len
                    else:
                        self.window["recv_buf"] = b""
                        self.window["recv_len"] = 0
            else:
                print("ERROR: Unknown or unimplemented flag.")
                read_len = EXIT_ERROR
        finally:
            self.recv_lock.release()

        return read_len

    def send_segment(self, data):
        """
        Send data in MSS-sized segments with SACK-aware retransmission.
        """
        offset = 0
        total_len = len(data)

        while offset < total_len:
            payload_len = min(MSS, total_len - offset)
            seq_no = self.window["next_seq_to_send"]
            chunk = data[offset:offset + payload_len]

            segment = Packet(seq=seq_no, ack=self.window["last_ack"], flags=0, payload=chunk)
            
            # Store segment for potential retransmission
            self.unacked_segments[seq_no] = segment

            ack_goal = seq_no + payload_len

            while True:
                print(f"Sending segment (seq={seq_no}, len={payload_len})")
                self.sock_fd.sendto(segment.encode(), self.conn)

                if self.wait_for_ack(ack_goal):
                    print(f"Segment {seq_no} acknowledged.")
                    # Remove from unacked segments
                    if seq_no in self.unacked_segments:
                        del self.unacked_segments[seq_no]
                    self.window["next_seq_to_send"] += payload_len
                    break
                else:
                    print("Timeout: Retransmitting segment.")

            offset += payload_len

    def wait_for_ack(self, ack_goal):
        with self.recv_lock:
            start = time.time()
            while self.window["next_seq_expected"] < ack_goal:
                elapsed = time.time() - start
                remaining = DEFAULT_TIMEOUT - elapsed
                if remaining <= 0:
                    return False
                self.wait_cond.wait(timeout=remaining)
            return True

    def _process_ack(self, ack_packet):
        """
        Process incoming ACK with SACK support and duplicate ACK detection.
        """
        ack = ack_packet.ack

        # Process SACK blocks - remove selectively acknowledged segments
        if ack_packet.flags & SACK_FLAG:
            for start, end in ack_packet.sack_blocks:
                # Remove all segments covered by this SACK block
                for seq in list(self.unacked_segments.keys()):
                    seg = self.unacked_segments[seq]
                    seg_end = seq + len(seg.payload)
                    if seq >= start and seg_end <= end:
                        print(f"SACK: Removing segment {seq} from unacked list")
                        del self.unacked_segments[seq]

        # Handle cumulative ACK - remove all segments before ACK
        for seq in list(self.unacked_segments.keys()):
            if seq < ack:
                print(f"ACK: Removing segment {seq} from unacked list")
                del self.unacked_segments[seq]

        # Duplicate ACK detection (TCP Reno behavior)
        if ack == self.last_ack_received and ack < self.window["next_seq_to_send"]:
            # This is a duplicate ACK
            self.duplicate_acks[ack] += 1
            print(f"Duplicate ACK #{self.duplicate_acks[ack]} for seq {ack}")
            
            if self.duplicate_acks[ack] == 3:
                # Triple duplicate ACK - fast retransmit
                print(f"Triple duplicate ACK for {ack}, triggering fast retransmit")
                if ack in self.unacked_segments:
                    segment = self.unacked_segments[ack]
                    self.sock_fd.sendto(segment.encode(), self.conn)
                    print(f"Fast retransmit: segment {ack}")
        else:
            # New ACK - reset duplicate count
            self.duplicate_acks.clear()
            self.last_ack_received = ack

        # Update next_seq_expected
        if ack > self.window["next_seq_expected"]:
            self.window["next_seq_expected"] = ack

    def _generate_sack_blocks(self):
        """
        Generate SACK blocks from out-of-order buffer.
        Returns list of (start, end) tuples representing received but non-contiguous data.
        """
        if not self.out_of_order_buffer:
            return []

        sack_blocks = []
        sorted_seqs = sorted(self.out_of_order_buffer.keys())
        
        block_start = sorted_seqs[0]
        block_end = block_start + len(self.out_of_order_buffer[block_start])

        for seq in sorted_seqs[1:]:
            payload_len = len(self.out_of_order_buffer[seq])
            if seq == block_end:
                # Contiguous with current block
                block_end = seq + payload_len
            else:
                # Gap found, save current block and start new one
                sack_blocks.append((block_start, block_end))
                block_start = seq
                block_end = seq + payload_len

        sack_blocks.append((block_start, block_end))
        return sack_blocks

    def _deliver_contiguous_data(self):
        """
        Move contiguous data from out-of-order buffer to receive buffer.
        """
        while self.window["last_ack"] in self.out_of_order_buffer:
            payload = self.out_of_order_buffer.pop(self.window["last_ack"])
            self.window["recv_buf"] += payload
            self.window["recv_len"] += len(payload)
            self.window["last_ack"] += len(payload)

    def backend(self):
        """
        Backend loop to handle receiving data and sending acknowledgments.
        """
        while not self.dying:
            try:
                data, addr = self.sock_fd.recvfrom(2048)
                packet = Packet.decode(data)

                if self.conn is None:
                    self.conn = addr

                # Handle ACK packets
                if (packet.flags & ACK_FLAG) != 0:
                    with self.recv_lock:
                        self._process_ack(packet)
                        self.wait_cond.notify_all()
                    continue

                # Handle data packets
                with self.recv_lock:
                    if packet.seq == self.window["last_ack"]:
                        # In-order packet
                        self.window["recv_buf"] += packet.payload
                        self.window["recv_len"] += len(packet.payload)
                        self.window["last_ack"] += len(packet.payload)
                        
                        # Check if we can deliver buffered out-of-order data
                        self._deliver_contiguous_data()
                        
                        print(f"Received in-order segment {packet.seq} with {len(packet.payload)} bytes.")
                    
                    elif packet.seq > self.window["last_ack"]:
                        # Out-of-order packet - buffer it
                        self.out_of_order_buffer[packet.seq] = packet.payload
                        print(f"Buffered out-of-order segment {packet.seq}, expected {self.window['last_ack']}")
                    
                    # else: duplicate/old packet, ignore but still ACK

                    # Generate SACK blocks for non-contiguous data
                    sack_blocks = self._generate_sack_blocks()
                    flags = ACK_FLAG | (SACK_FLAG if sack_blocks else 0)
                    
                    # Send ACK with SACK blocks
                    ack_packet = Packet(
                        seq=0, 
                        ack=self.window["last_ack"], 
                        flags=flags, 
                        sack_blocks=sack_blocks
                    )
                    self.sock_fd.sendto(ack_packet.encode(), addr)
                    print(f"Sent ACK {self.window['last_ack']} with SACK blocks {sack_blocks}")

                with self.wait_cond:
                    self.wait_cond.notify_all()

            except socket.timeout:
                continue

            except Exception as e:
                if not self.dying:
                    print(f"Error in backend: {e}")

