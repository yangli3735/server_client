# Simplified TCP Implementation over UDP

## Implementation Description

This project implements a simplified TCP protocol over UDP sockets with the following features:

- **Connection Management**: 3-way handshake for connection establishment and 4-way FIN handshake for graceful termination
- **Reliable Data Transfer**: Sliding window sender with cumulative ACKs and pipelined transmission
- **Flow Control**: Peer advertised window (16-bit) enforced per-packet
- **Data Buffering**: Receive buffer with out-of-order segment handling (up to 65535 bytes total)
- **Loss Recovery**:
  - Timeout-based retransmission using dynamic RTO (RTO = 2 × EstimatedRTT)
  - RTT estimation via EWMA (α = 0.875)
  - Fast retransmit on triple duplicate ACKs (TCP Reno)
  - SACK (Selective Acknowledgment) support with single 64-bit block in header
- **Packet Format**: Fixed 22-byte header (seq, ack, flags, window, sack_left, sack_right) + variable payload

## Assumptions

- **Network**: UDP over localhost (127.0.0.1) on port 54321
- **Maximum Buffer**: 65535 bytes total receive buffer (contiguous + out-of-order combined)
- **MSS**: 1376 bytes (Maximum Segment Size)
- **Segment Timeout**: 3 seconds default (adaptive based on measured RTT)
- **Handshake Robustness**: Server transitions to ESTABLISHED upon receiving data in SYN_RCVD state (handles loss of final ACK)
- **SACK Semantics**: One block per ACK; dual ACK support for old/duplicate segments enables retransmission loop prevention
- **Deadlines**: TIME_WAIT duration is 2 × DEFAULT_TIMEOUT seconds

## How to Run

1. **Setup**: Navigate to the project folder:
   ```bash
   project2-checkpoint1
   ```


2. **Run server and client in separate terminals**:
   ```bash
   # Terminal 1: Start server
   python3 server.py
   
   # Terminal 2: Start client
   python3 client.py
   ```

3. **Optional: Test under network conditions** (requires `sudo` for tc):
   ```bash
   bash simulate_loss_delay.sh 
   ```
   - This applies 50ms delay + 20% loss to loopback interface
   - Starts server in background, runs client, then cleans up tc rules on exit
   - Edit `DELAY` and `LOSS` variables in the script to test different scenarios

### Network Condition Reference

| Scenario            | DELAY   | LOSS  | Manual tc command                                          |
|---------------------|---------|-------|------------------------------------------------------------|
| Baseline            | `0ms`   | `0%`  | *(no rule needed)*                                         |
| Low delay           | `50ms`  | `0%`  | `sudo tc qdisc add dev lo root netem delay 50ms`           |
| High delay          | `200ms` | `0%`  | `sudo tc qdisc add dev lo root netem delay 200ms`          |
| Low loss            | `0ms`   | `5%`  | `sudo tc qdisc add dev lo root netem loss 5%`              |
| Medium loss         | `0ms`   | `10%` | `sudo tc qdisc add dev lo root netem loss 10%`             |
| High loss           | `0ms`   | `20%` | `sudo tc qdisc add dev lo root netem loss 20%`             |
| Delay + loss        | `50ms`  | `5%`  | `sudo tc qdisc add dev lo root netem delay 50ms loss 5%`   |
| Harsh conditions    | `200ms` | `20%` | `sudo tc qdisc add dev lo root netem delay 200ms loss 20%` |