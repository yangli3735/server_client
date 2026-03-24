import unittest
import subprocess
import time

class TestNetworkConditions(unittest.TestCase):
    def setUp(self):
        self.simulate_command = "sudo tc qdisc add dev lo root netem"
        self.restore_command = "sudo tc qdisc del dev lo root netem"

    def simulate_conditions(self, delay, loss):
        command = f"{self.simulate_command} delay {delay}ms loss {loss}%"
        subprocess.run(command, shell=True)

    def test_network_conditions(self):
        conditions = [
            (100, 10),
            (50, 5),
            (200, 20)
        ]
        
        for delay, loss in conditions:
            self.simulate_conditions(delay, loss)
            time.sleep(2)  # Allow time for conditions to take effect
            
            # Here you would run your client and server code to test the TCP implementation
            
            # Example: subprocess.run(["python3", "client.py"])
            # Add assertions to check the behavior of your TCP implementation
            
            subprocess.run(self.restore_command, shell=True)

if __name__ == '__main__':
    unittest.main()