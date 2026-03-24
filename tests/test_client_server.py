import unittest
import subprocess
import time

class TestClientServer(unittest.TestCase):
    def setUp(self):
        self.server_process = subprocess.Popen(['python', 'server.py'])
        time.sleep(1)

    def tearDown(self):
        self.server_process.terminate()
        self.server_process.wait()

    def test_normal_conditions(self):
        result = subprocess.run(['python', 'client.py', 'Hello, Server!'], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'Hello, Client!')

    def test_packet_loss(self):
        subprocess.run(['sudo', 'tc', 'qdisc', 'add', 'dev', 'lo', 'root', 'netem', 'loss', '10%'])
        result = subprocess.run(['python', 'client.py', 'Test packet loss'], capture_output=True, text=True)
        self.assertIn('Error', result.stdout)
        subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', 'lo', 'root', 'netem'])

    def test_delay(self):
        subprocess.run(['sudo', 'tc', 'qdisc', 'add', 'dev', 'lo', 'root', 'netem', 'delay', '100ms'])
        result = subprocess.run(['python', 'client.py', 'Test delay'], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'Hello, Client!')
        subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', 'lo', 'root', 'netem'])

if __name__ == '__main__':
    unittest.main()