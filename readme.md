 ## How to Run

option1 
1. Go to the project runtime folder:
	cd /home/yangli/network/server_client/proj2-check1

2. Ensure input file exists for the client:
	- large_test.txt (read by client.py)

3. Run server and client in separate terminals:
	- Terminal 1:
	  python3 server.py
	- Terminal 2:
	  python3 client.py

Option2
 run with simulated delay/loss (uses sudo tc on loopback interface lo):
	b
## Testing Under Different Network Conditions

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