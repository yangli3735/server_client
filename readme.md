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

## Program Environment

**Python Version**: Python 3.6 or higher
- Uses f-string formatting and socket/threading APIs available in Python 3.6+

**Linux Kernel**: Modern Linux distribution (e.g., Linux 5.x or later)
- Required for `tc` (traffic control) command when testing with network conditions
- Uses standard UDP socket APIs (POSIX-compatible)

**Dependencies**: None (standard library only)
- `socket`: UDP communication
- `struct`: Binary packet encoding/decoding
- `threading`: Concurrent server and client handling
- `time`: RTT measurement and timeout management
- `random`, `string`: Test data generation

## How to Run

option1 
1. Go to the project runtime folder:
    Run server and client in separate terminals:
	- Terminal 1:
	  python3 server.py
	- Terminal 2:
	  python3 client.py


2. Run the following command in terminal3: 

    sudo tc qdisc add dev lo root netem delay 50ms  loss 5%
   
   Change different delay and loss variables to test different conditions. 

3. Restoring Normal Network Conditions 

    When you’re finished testing, remove the network emulation with: 
    sudo tc qdisc del dev lo root netem 

Option2
 run with simulated delay/loss (uses sudo tc on loopback interface lo):

Edit `DELAY` and `LOSS` at the top of `simulate_loss_delay.sh`, then rerun:

bash simulate_loss_delay.sh

```bash
DELAY="50ms"   # change this
LOSS="5%"      # change this
```

### Scenario Reference

| Scenario            | DELAY   | LOSS  | Manual tc command                                          |
|---------------------|---------|-------|------------------------------------------------------------|
| 1. Baseline         | `0ms`   | `0%`  | *(no tc rule needed)*                                      |
| 2. Low delay        | `50ms`  | `0%`  | `sudo tc qdisc add dev lo root netem delay 50ms`           |
| 3. High delay       | `200ms` | `0%`  | `sudo tc qdisc add dev lo root netem delay 200ms`          |
| 4. Low loss         | `0ms`   | `5%`  | `sudo tc qdisc add dev lo root netem loss 5%`              |
| 5. Medium loss      | `0ms`   | `10%` | `sudo tc qdisc add dev lo root netem loss 10%`             |
| 6. High loss        | `0ms`   | `20%` | `sudo tc qdisc add dev lo root netem loss 20%`             |
| 7. Delay + loss     | `50ms`  | `5%`  | `sudo tc qdisc add dev lo root netem delay 50ms loss 5%`   |
| 8. Harsh conditions | `200ms` | `20%` | `sudo tc qdisc add dev lo root netem delay 200ms loss 20%` |

Notes:
- server.py listens on port 54321.
- simulate_loss_delay.sh starts server.py in background, then runs client.py, and removes netem settings on exit.