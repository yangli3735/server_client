import socket
import struct
import threading
import time  
from grading import MSS, DEFAULT_TIMEOUT, MAX_NETWORK_BUFFER

# Constants for simplified TCP
SYN_FLAG = 0x8   # Synchronization flag 
ACK_FLAG = 0x4   # Acknowledgment flag
FIN_FLAG = 0x2   # Finish flag 
SACK_FLAG = 0x1  # Selective Acknowledgment flag 

EXIT_SUCCESS = 0
EXIT_ERROR = 1

STATE_CLOSED = "CLOSED"
STATE_LISTEN = "LISTEN"
STATE_SYN_SENT = "SYN_SENT"
STATE_SYN_RCVD = "SYN_RCVD"
STATE_ESTABLISHED = "ESTABLISHED"
STATE_FIN_SENT = "FIN_SENT"
STATE_CLOSE_WAIT = "CLOSE_WAIT"
STATE_LAST_ACK = "LAST_ACK"
STATE_TIME_WAIT = "TIME_WAIT"
TIME_WAIT_DURATION = 2 * DEFAULT_TIMEOUT
RTT_ALPHA = 0.875
MIN_RTO = 0.1


def log_with_timestamp(message):
    now = time.time()
    timestamp = time.strftime("%H:%M:%S", time.localtime(now))
    milliseconds = int((now % 1) * 1000)
    print(f"[{timestamp}.{milliseconds:03d}] {message}")

class ReadMode:
    NO_FLAG = 0
    NO_WAIT = 1
    TIMEOUT = 2

class Packet:
    def __init__(self, seq=0, ack=0, flags=0, window=0, payload=b""):
        self.seq = seq
        self.ack = ack
        self.flags = flags
        self.window = window
        self.payload = payload

    def encode(self):
        # Encode the packet header and payload into bytes
        header = struct.pack("!IIIIH", self.seq, self.ack, self.flags, self.window, len(self.payload))
        return header + self.payload

    @staticmethod
    def decode(data):
        # Decode bytes into a Packet object
        header_size = struct.calcsize("!IIIIH")
        seq, ack, flags, window, payload_len = struct.unpack("!IIIIH", data[:header_size])
        payload = data[header_size:]
        return Packet(seq, ack, flags, window, payload)


class TransportSocket:
    def __init__(self):
        self.sock_fd = None

        # Locks and condition
        self.recv_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.wait_cond = threading.Condition(self.recv_lock)

        self.death_lock = threading.Lock()
        self.dying = False
        self.thread = None

        self.window = {
            "last_ack": 0,            # The next seq we expect from peer (used for receiving data)
            "next_seq_expected": 0,   # The highest ack we've received for *our* transmitted data
            "recv_buf": b"",          # Received data buffer
            "recv_len": 0,            # How many bytes are in recv_buf
            "next_seq_to_send": 0,    # The sequence number for the next packet we send
            "send_base": 0,           # Left edge of sender window (earliest unacknowledged byte)
            "peer_advertised_window": MAX_NETWORK_BUFFER,
        }
        self.unacked_segments = {}     # seq -> {"packet": Packet, "len": int, "last_tx": float}
        self.out_of_order_segments = {}
        self.out_of_order_bytes = 0
        self.sock_type = None
        self.conn = None
        self.my_port = None
        self.state = STATE_CLOSED
        self.time_wait_deadline = None
        self.estimated_rtt = None
        self.retransmission_timeout = DEFAULT_TIMEOUT
        self.last_duplicate_ack = None
        self.duplicate_ack_count = 0

    def _set_state(self, new_state):
        if self.state != new_state:
            log_with_timestamp(f"State transition: {self.state} -> {new_state}")
            self.state = new_state
        self.wait_cond.notify_all()

    def _send_control_packet(self, flags, seq=None, ack=None, addr=None):
        packet = Packet(
            seq=self.window["next_seq_to_send"] if seq is None else seq,
            ack=self.window["last_ack"] if ack is None else ack,
            flags=flags,
            window=self._current_advertised_window(),
        )
        self.sock_fd.sendto(packet.encode(), self.conn if addr is None else addr)

    def _buffered_receive_bytes(self):
        return self.window["recv_len"] + self.out_of_order_bytes

    def _serialize_sack_blocks(self, sack_blocks):
        payload = bytearray()
        for start, end in sack_blocks:
            payload.extend(struct.pack("!II", start, end))
        return bytes(payload)

    def _parse_sack_blocks(self, payload):
        block_size = struct.calcsize("!II")
        usable_length = len(payload) - (len(payload) % block_size)
        sack_blocks = []
        for offset in range(0, usable_length, block_size):
            start, end = struct.unpack("!II", payload[offset : offset + block_size])
            if start < end:
                sack_blocks.append((start, end))
        return sack_blocks

    def _current_sack_blocks_locked(self):
        merged_blocks = []
        for seq in sorted(self.out_of_order_segments.keys()):
            end = seq + len(self.out_of_order_segments[seq])
            if merged_blocks and seq <= merged_blocks[-1][1]:
                merged_blocks[-1] = (merged_blocks[-1][0], max(merged_blocks[-1][1], end))
            else:
                merged_blocks.append((seq, end))
        return merged_blocks

    def _current_advertised_window(self):
        return max(0, MAX_NETWORK_BUFFER - self._buffered_receive_bytes())

    def _send_ack_packet(self, ack_val=None, addr=None, include_sack=False):
        sack_blocks = self._current_sack_blocks_locked() if include_sack else []
        flags = ACK_FLAG | (SACK_FLAG if sack_blocks else 0)
        packet = Packet(
            seq=self.window["next_seq_to_send"],
            ack=self.window["last_ack"] if ack_val is None else ack_val,
            flags=flags,
            window=self._current_advertised_window(),
            payload=self._serialize_sack_blocks(sack_blocks),
        )
        self.sock_fd.sendto(packet.encode(), self.conn if addr is None else addr)

    def _in_flight_bytes(self):
        return max(0, self.window["next_seq_to_send"] - self.window["send_base"])

    def _available_send_window(self):
        in_flight = self._in_flight_bytes()
        return max(0, self.window["peer_advertised_window"] - in_flight)

    def _prune_acked_segments(self):
        acked_seqs = [
            seq for seq, info in self.unacked_segments.items()
            if (seq + info["len"]) <= self.window["send_base"]
        ]
        for seq in acked_seqs:
            del self.unacked_segments[seq]

    def _advance_send_base(self, ack_val):
        clamped_ack = max(0, min(ack_val, self.window["next_seq_to_send"]))
        if clamped_ack <= self.window["send_base"]:
            return False

        old_base = self.window["send_base"]
        self._record_acked_rtt_sample(old_base, clamped_ack)
        self.window["send_base"] = clamped_ack
        self.window["next_seq_expected"] = max(self.window["next_seq_expected"], clamped_ack)
        self._prune_acked_segments()
        log_with_timestamp(
            f"ACK advanced send_base: {old_base} -> {self.window['send_base']}"
        )
        self._log_flow_state("flow-update")
        return True

    def _log_flow_state(self, prefix):
        in_flight = self._in_flight_bytes()
        log_with_timestamp(
            f"{prefix} | peer_win={self.window['peer_advertised_window']} "
            f"send_base={self.window['send_base']} next_seq={self.window['next_seq_to_send']} "
            f"in_flight={in_flight} recv_len={self.window['recv_len']} ooo_bytes={self.out_of_order_bytes} "
            f"adv_win={self._current_advertised_window()} rto={self.retransmission_timeout:.3f}s"
        )

    def _segment_is_sacked(self, seq, segment_len, sack_blocks):
        segment_end = seq + segment_len
        for block_start, block_end in sack_blocks:
            if block_start <= seq and segment_end <= block_end:
                return True
        return False

    def _update_sacked_segments(self, sack_blocks):
        if not sack_blocks:
            return

        for seq, info in self.unacked_segments.items():
            if self._segment_is_sacked(seq, info["len"], sack_blocks):
                info["sacked"] = True

    def _retransmit_segment_locked(self, seq, reason):
        info = self.unacked_segments.get(seq)
        if info is None:
            return False

        self.sock_fd.sendto(info["packet"].encode(), self.conn)
        info["last_tx"] = time.time()
        info["retransmitted"] = True
        log_with_timestamp(f"{reason}: retransmitting segment seq={seq}, len={info['len']}")
        self._log_flow_state("fast-retransmit")
        return True

    def _segment_overlaps_buffered_data_locked(self, seq, payload_len):
        segment_end = seq + payload_len
        if segment_end <= self.window["last_ack"]:
            return True

        for existing_seq, payload in self.out_of_order_segments.items():
            existing_end = existing_seq + len(payload)
            if not (segment_end <= existing_seq or seq >= existing_end):
                return True

        return False

    def _drain_contiguous_out_of_order_locked(self):
        drained = 0
        while self.window["last_ack"] in self.out_of_order_segments:
            seq = self.window["last_ack"]
            payload = self.out_of_order_segments.pop(seq)
            self.out_of_order_bytes -= len(payload)
            self.window["recv_buf"] += payload
            self.window["recv_len"] += len(payload)
            self.window["last_ack"] += len(payload)
            drained += len(payload)

        if drained > 0:
            log_with_timestamp(f"Drained {drained} bytes from out-of-order buffer into recv buffer")

    def _update_rtt_estimate(self, sample_rtt):
        if sample_rtt <= 0:
            return

        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt
        else:
            self.estimated_rtt = (
                RTT_ALPHA * self.estimated_rtt + (1 - RTT_ALPHA) * sample_rtt
            )

        self.retransmission_timeout = max(MIN_RTO, 2 * self.estimated_rtt)
        log_with_timestamp(
            f"RTT update: sample={sample_rtt:.3f}s estimated={self.estimated_rtt:.3f}s "
            f"rto={self.retransmission_timeout:.3f}s alpha={RTT_ALPHA}"
        )

    def _record_acked_rtt_sample(self, old_send_base, new_send_base):
        sample_candidates = []
        for seq, info in self.unacked_segments.items():
            segment_end = seq + info["len"]
            if old_send_base < segment_end <= new_send_base and not info["retransmitted"]:
                sample_candidates.append((segment_end, info["first_tx"]))

        if not sample_candidates:
            return

        _, first_tx = min(sample_candidates, key=lambda item: item[0])
        self._update_rtt_estimate(time.time() - first_tx)

    def _wait_for_send_window(self):
        with self.recv_lock:
            while self._available_send_window() <= 0:
                if self.state not in {STATE_ESTABLISHED, STATE_CLOSE_WAIT}:
                    return 0
                self.wait_cond.wait(timeout=DEFAULT_TIMEOUT)
            return self._available_send_window()

    def _wait_for_state(self, target_states, timeout=None):
        target_states = set(target_states)
        with self.recv_lock:
            start = time.time()
            while self.state not in target_states:
                if timeout is None:
                    self.wait_cond.wait()
                    continue

                elapsed = time.time() - start
                remaining = timeout - elapsed
                if remaining <= 0:
                    return False
                self.wait_cond.wait(timeout=remaining)
            return True

    def _enter_time_wait(self):
        self.time_wait_deadline = time.time() + TIME_WAIT_DURATION
        self._set_state(STATE_TIME_WAIT)

    def _initiate_connection(self):
        with self.recv_lock:
            self._set_state(STATE_SYN_SENT)

        while True:
            with self.recv_lock:
                if self.state == STATE_ESTABLISHED:
                    return EXIT_SUCCESS
            log_with_timestamp("Sending SYN")
            self._send_control_packet(SYN_FLAG, ack=0)
            with self.recv_lock:
                self.window["next_seq_to_send"] = 1
            if self._wait_for_state({STATE_ESTABLISHED}, timeout=DEFAULT_TIMEOUT):
                return EXIT_SUCCESS
            log_with_timestamp("Timeout waiting for SYN-ACK, retransmitting SYN")

    def socket(self, sock_type, port, server_ip=None):
        """
        Create and initialize the socket, setting its type and starting the backend thread.
        """
        self.sock_fd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_type = sock_type

        if sock_type == "TCP_INITIATOR":
            self.conn = (server_ip, port)
            self.sock_fd.bind(("", 0))  # Bind to any available local port
        elif sock_type == "TCP_LISTENER":
            self.sock_fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock_fd.bind(("", port))
        else:
            log_with_timestamp("Unknown socket type")
            return EXIT_ERROR

        # 1-second timeout so we can periodically check `self.dying`
        self.sock_fd.settimeout(1.0)

        self.my_port = self.sock_fd.getsockname()[1]

        # Start the backend thread
        self.thread = threading.Thread(target=self.backend, daemon=True)
        self.thread.start()

        if sock_type == "TCP_INITIATOR":
            return self._initiate_connection()

        with self.recv_lock:
            self._set_state(STATE_LISTEN)
        return EXIT_SUCCESS

    def close(self):
        """
        Close the socket and stop the backend thread.
        """
        if self.sock_fd is None:
            log_with_timestamp("Error: Null socket")
            return EXIT_ERROR

        wait_for_peer = False
        wait_timeout = None

        with self.recv_lock:
            if self.state == STATE_ESTABLISHED:
                log_with_timestamp("Sending FIN")
                self._send_control_packet(FIN_FLAG)
                self.window["next_seq_to_send"] += 1
                self._set_state(STATE_FIN_SENT)
                wait_for_peer = True
            elif self.state == STATE_CLOSE_WAIT:
                log_with_timestamp("Sending FIN from CLOSE_WAIT")
                self._send_control_packet(FIN_FLAG)
                self.window["next_seq_to_send"] += 1
                self._set_state(STATE_LAST_ACK)
                wait_for_peer = True
            elif self.state == STATE_TIME_WAIT:
                wait_for_peer = True
                if self.time_wait_deadline is not None:
                    wait_timeout = max(0, self.time_wait_deadline - time.time())
            elif self.state in {STATE_LISTEN, STATE_CLOSED}:
                self._set_state(STATE_CLOSED)
            elif self.state == STATE_SYN_SENT:
                self._set_state(STATE_CLOSED)

        if wait_for_peer:
            self._wait_for_state({STATE_TIME_WAIT, STATE_CLOSED}, timeout=DEFAULT_TIMEOUT)

            with self.recv_lock:
                if self.state == STATE_TIME_WAIT:
                    if wait_timeout is None and self.time_wait_deadline is not None:
                        wait_timeout = max(0, self.time_wait_deadline - time.time())
                    if wait_timeout is None:
                        wait_timeout = TIME_WAIT_DURATION

            self._wait_for_state({STATE_CLOSED}, timeout=wait_timeout)

            with self.recv_lock:
                if self.state == STATE_TIME_WAIT:
                    self._set_state(STATE_CLOSED)

        self.death_lock.acquire()
        try:
            self.dying = True
        finally:
            self.death_lock.release()

        with self.recv_lock:
            self.wait_cond.notify_all()

        if self.thread:
            self.thread.join()

        self.sock_fd.close()

        return EXIT_SUCCESS

    def send(self, data):
        """
        Send data reliably using sliding-window flow control.
        """
        if not self.conn:
            raise ValueError("Connection not established.")
        with self.send_lock:
            self.send_segment(data)

    def recv(self, buf, length, flags):
        """
        Retrieve received data from the buffer, with optional blocking behavior.

        :param buf: Buffer to store received data (list of bytes or bytearray).
        :param length: Maximum length of data to read
        :param flags: ReadMode flag to control blocking behavior
        :return: Number of bytes read
        """
        read_len = 0

        if length < 0:
            log_with_timestamp("ERROR: Negative length")
            return EXIT_ERROR

        # If blocking read, wait until there's data in buffer
        if flags == ReadMode.NO_FLAG:
            with self.wait_cond:
                while self.window["recv_len"] == 0 and self.state not in {STATE_CLOSE_WAIT, STATE_LAST_ACK, STATE_TIME_WAIT, STATE_CLOSED}:
                    self.wait_cond.wait()

        self.recv_lock.acquire()
        try:
            if flags in [ReadMode.NO_WAIT, ReadMode.NO_FLAG]:
                if self.window["recv_len"] > 0:
                    read_len = min(self.window["recv_len"], length)
                    buf[0] = self.window["recv_buf"][:read_len]

                    # Remove data from the buffer
                    if read_len < self.window["recv_len"]:
                        self.window["recv_buf"] = self.window["recv_buf"][read_len:]
                        self.window["recv_len"] -= read_len
                    else:
                        self.window["recv_buf"] = b""
                        self.window["recv_len"] = 0

                    if self.conn and self.state in {STATE_ESTABLISHED, STATE_CLOSE_WAIT, STATE_FIN_SENT, STATE_LAST_ACK}:
                        self._send_ack_packet(ack_val=self.window["last_ack"], include_sack=bool(self.out_of_order_segments))
                        self._log_flow_state("recv-read window-update")
                        self.wait_cond.notify_all()
            else:
                log_with_timestamp("ERROR: Unknown or unimplemented flag.")
                read_len = EXIT_ERROR
        finally:
            self.recv_lock.release()

        return read_len

    def send_segment(self, data):
        """
        Send data using a sliding-window sender:
        - Send continuously while in-flight bytes are below peer advertised window.
        - Track outstanding segments and rely on cumulative ACKs.
        - Retransmit unacknowledged data after timeout.
        """
        offset = 0
        total_len = len(data)
        if total_len == 0:
            return

        with self.recv_lock:
            send_start_seq = self.window["next_seq_to_send"]
        final_ack_goal = send_start_seq + total_len

        with self.recv_lock:
            self._prune_acked_segments()

        while True:
            with self.recv_lock:
                if self.state not in {STATE_ESTABLISHED, STATE_CLOSE_WAIT}:
                    log_with_timestamp("Send aborted: connection not in a sendable state")
                    return

                # Fill the peer-advertised window as much as possible.
                while offset < total_len and self._available_send_window() > 0:
                    payload_len = min(MSS, total_len - offset, self._available_send_window())
                    if payload_len <= 0:
                        break

                    seq_no = self.window["next_seq_to_send"]
                    chunk = data[offset : offset + payload_len]
                    segment = Packet(
                        seq=seq_no,
                        ack=self.window["last_ack"],
                        flags=0,
                        window=self._current_advertised_window(),
                        payload=chunk,
                    )

                    log_with_timestamp(f"Sending segment (seq={seq_no}, len={payload_len})")
                    self.sock_fd.sendto(segment.encode(), self.conn)

                    send_time = time.time()

                    self.unacked_segments[seq_no] = {
                        "packet": segment,
                        "len": payload_len,
                        "first_tx": send_time,
                        "last_tx": send_time,
                        "retransmitted": False,
                        "sacked": False,
                    }
                    self.window["next_seq_to_send"] += payload_len
                    offset += payload_len
                    self._log_flow_state("send")

                if self.window["send_base"] >= final_ack_goal:
                    log_with_timestamp(f"All data acknowledged up to seq={self.window['send_base']}.")
                    return

                # If we have outstanding data, use oldest segment timer for retransmission.
                if self.unacked_segments:
                    oldest_seq = min(self.unacked_segments.keys())
                    oldest_info = self.unacked_segments[oldest_seq]
                    elapsed = time.time() - oldest_info["last_tx"]
                    remaining = self.retransmission_timeout - elapsed

                    if remaining <= 0:
                        log_with_timestamp("Timeout: retransmitting unacknowledged window.")
                        retransmitted_any = False
                        for seq in sorted(self.unacked_segments.keys()):
                            if self.unacked_segments[seq].get("sacked"):
                                continue
                            pkt = self.unacked_segments[seq]["packet"]
                            self.sock_fd.sendto(pkt.encode(), self.conn)
                            self.unacked_segments[seq]["last_tx"] = time.time()
                            self.unacked_segments[seq]["retransmitted"] = True
                            retransmitted_any = True
                        if not retransmitted_any and self.window["send_base"] in self.unacked_segments:
                            self._retransmit_segment_locked(self.window["send_base"], "Timeout fallback")
                        self._log_flow_state("retransmit")
                        continue

                    if offset < total_len and self._available_send_window() <= 0:
                        log_with_timestamp("Window full, waiting for ACK/window update")

                    self.wait_cond.wait(timeout=remaining)
                else:
                    if offset < total_len and self.window["peer_advertised_window"] == 0:
                        log_with_timestamp("Peer advertised zero window, waiting for window update")
                    # Nothing in-flight and no send window yet, wait for ACK/window update.
                    self.wait_cond.wait(timeout=self.retransmission_timeout)


    def wait_for_ack(self, ack_goal):
        """
        Wait for 'next_seq_expected' to reach or exceed 'ack_goal' within DEFAULT_TIMEOUT.
        Return True if ack arrived in time; False on timeout.
        """
        with self.recv_lock:
            start = time.time()
            while self.window["next_seq_expected"] < ack_goal:
                elapsed = time.time() - start
                remaining = self.retransmission_timeout - elapsed
                if remaining <= 0:
                    return False

                self.wait_cond.wait(timeout=remaining)

            return True

    def backend(self):
        """
        Backend loop to handle receiving data and sending acknowledgments.
        All incoming packets are read in this thread only, to avoid concurrency conflicts.
        """
        while not self.dying:
            try:
                data, addr = self.sock_fd.recvfrom(2048)
                packet = Packet.decode(data)

                # If no peer is set, establish connection (for listener)
                if self.conn is None:
                    self.conn = addr

                if packet.flags == (SYN_FLAG | ACK_FLAG):
                    with self.recv_lock:
                        if self.state == STATE_SYN_SENT:
                            self.window["last_ack"] = packet.seq + 1
                            if packet.ack > self.window["next_seq_expected"]:
                                self.window["next_seq_expected"] = packet.ack
                            log_with_timestamp("Received SYN-ACK")
                            self._send_ack_packet(ack_val=self.window["last_ack"])
                            self._set_state(STATE_ESTABLISHED)
                        elif self.state == STATE_ESTABLISHED:
                            self._send_ack_packet(ack_val=packet.seq + 1)
                    continue

                if (packet.flags & SYN_FLAG) != 0:
                    with self.recv_lock:
                        if self.state == STATE_LISTEN:
                            self.conn = addr
                            self.window["last_ack"] = packet.seq + 1
                            log_with_timestamp("Received SYN")
                            self._send_control_packet(SYN_FLAG | ACK_FLAG, ack=self.window["last_ack"], addr=addr)
                            self.window["next_seq_to_send"] = 1
                            self._set_state(STATE_SYN_RCVD)
                        elif self.state == STATE_SYN_RCVD:
                            self._send_control_packet(SYN_FLAG | ACK_FLAG, ack=packet.seq + 1, addr=addr)
                    continue

                if (packet.flags & FIN_FLAG) != 0:
                    with self.recv_lock:
                        self.window["last_ack"] = packet.seq + 1
                        log_with_timestamp("Received FIN")
                        self._send_ack_packet(ack_val=self.window["last_ack"], addr=addr)

                        if self.state == STATE_ESTABLISHED:
                            self._set_state(STATE_CLOSE_WAIT)
                        elif self.state == STATE_FIN_SENT:
                            self._enter_time_wait()
                    continue

                # If it's an ACK packet, update our sending side
                if (packet.flags & ACK_FLAG) != 0:
                    with self.recv_lock:
                        sack_blocks = self._parse_sack_blocks(packet.payload) if (packet.flags & SACK_FLAG) != 0 else []
                        prev_peer_window = self.window["peer_advertised_window"]
                        self.window["peer_advertised_window"] = max(0, min(MAX_NETWORK_BUFFER, packet.window))
                        self._update_sacked_segments(sack_blocks)

                        ack_advanced = self._advance_send_base(packet.ack)
                        if not ack_advanced and packet.ack > self.window["next_seq_expected"]:
                            self.window["next_seq_expected"] = packet.ack

                        if ack_advanced:
                            self.last_duplicate_ack = None
                            self.duplicate_ack_count = 0
                        elif packet.ack == self.window["send_base"] and self.window["send_base"] < self.window["next_seq_to_send"]:
                            if self.last_duplicate_ack == packet.ack:
                                self.duplicate_ack_count += 1
                            else:
                                self.last_duplicate_ack = packet.ack
                                self.duplicate_ack_count = 1

                            log_with_timestamp(
                                f"Duplicate ACK for {packet.ack} count={self.duplicate_ack_count}"
                            )
                            if self.duplicate_ack_count == 3:
                                self._retransmit_segment_locked(packet.ack, "Triple duplicate ACK")

                        if prev_peer_window != self.window["peer_advertised_window"]:
                            log_with_timestamp(
                                f"Peer window update: {prev_peer_window} -> {self.window['peer_advertised_window']}"
                            )
                            self._log_flow_state("peer-window")
                        if sack_blocks:
                            log_with_timestamp(f"Received SACK blocks: {sack_blocks}")
                        if self.state == STATE_SYN_RCVD and packet.ack >= self.window["next_seq_to_send"]:
                            log_with_timestamp("Received final ACK for handshake")
                            self._set_state(STATE_ESTABLISHED)
                        elif self.state == STATE_FIN_SENT and packet.ack >= self.window["next_seq_to_send"]:
                            log_with_timestamp("Received ACK for FIN")
                            self._enter_time_wait()
                        elif self.state == STATE_LAST_ACK and packet.ack >= self.window["next_seq_to_send"]:
                            log_with_timestamp("Received ACK for final FIN")
                            self._set_state(STATE_CLOSED)
                        self.wait_cond.notify_all()
                    continue

                # Otherwise, assume it is a data packet
                # Check if the sequence matches our 'last_ack' (in-order data)
                if packet.seq == self.window["last_ack"]:
                    with self.recv_lock:
                        available = MAX_NETWORK_BUFFER - self._buffered_receive_bytes()
                        payload_len = len(packet.payload)

                        if payload_len > available:
                            log_with_timestamp(
                                f"Receive buffer full (used={self._buffered_receive_bytes()}, max={MAX_NETWORK_BUFFER}), dropping segment seq={packet.seq}, len={payload_len}"
                            )
                            self._send_ack_packet(ack_val=self.window["last_ack"], addr=addr, include_sack=bool(self.out_of_order_segments))
                            self._log_flow_state("recv-drop window=0")
                            self.wait_cond.notify_all()
                            continue

                        # Append payload to our receive buffer
                        self.window["recv_buf"] += packet.payload
                        self.window["recv_len"] += payload_len
                        self.window["last_ack"] = packet.seq + payload_len
                        self._drain_contiguous_out_of_order_locked()
                        self._log_flow_state("recv-buffered")

                    with self.wait_cond:
                        self.wait_cond.notify_all()

                    log_with_timestamp(f"Received segment {packet.seq} with {len(packet.payload)} bytes.")

                    # Send back an acknowledgment
                    with self.recv_lock:
                        self._send_ack_packet(
                            ack_val=self.window["last_ack"],
                            addr=addr,
                            include_sack=bool(self.out_of_order_segments),
                        )
                elif packet.seq > self.window["last_ack"]:
                    with self.recv_lock:
                        payload_len = len(packet.payload)
                        available = MAX_NETWORK_BUFFER - self._buffered_receive_bytes()

                        if self._segment_overlaps_buffered_data_locked(packet.seq, payload_len):
                            log_with_timestamp(
                                f"Duplicate/out-of-order segment already buffered seq={packet.seq}, len={payload_len}"
                            )
                        elif payload_len > available:
                            log_with_timestamp(
                                f"Receive buffer full for out-of-order data (used={self._buffered_receive_bytes()}, max={MAX_NETWORK_BUFFER}), dropping seq={packet.seq}, len={payload_len}"
                            )
                        else:
                            self.out_of_order_segments[packet.seq] = packet.payload
                            self.out_of_order_bytes += payload_len
                            log_with_timestamp(
                                f"Buffered out-of-order segment seq={packet.seq}, len={payload_len}"
                            )
                            self._log_flow_state("recv-sack")

                        self._send_ack_packet(
                            ack_val=self.window["last_ack"],
                            addr=addr,
                            include_sack=True,
                        )
                        self.wait_cond.notify_all()
                else:
                    with self.recv_lock:
                        log_with_timestamp(
                            f"Duplicate old packet: seq={packet.seq}, expected={self.window['last_ack']}"
                        )
                        self._send_ack_packet(
                            ack_val=self.window["last_ack"],
                            addr=addr,
                            include_sack=bool(self.out_of_order_segments),
                        )

            except socket.timeout:
                with self.recv_lock:
                    if self.state == STATE_TIME_WAIT and self.time_wait_deadline is not None and time.time() >= self.time_wait_deadline:
                        self._set_state(STATE_CLOSED)
                continue
        
            except Exception as e:
                if not self.dying:
                    log_with_timestamp(f"Error in backend: {e}")

