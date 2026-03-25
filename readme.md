1. baseline
无 delay，无 loss


2. 低延迟
50ms delay

sudo tc qdisc add dev lo root netem delay 50ms

3.高延迟
200ms delay

sudo tc qdisc add dev lo root netem delay 200ms
4.低丢包
5% loss
sudo tc qdisc add dev lo root netem loss 5%
5.中等丢包
10% loss

sudo tc qdisc add dev lo root netem loss 10%
6.高丢包
20% loss

sudo tc qdisc add dev lo root netem loss 20%

7.低延迟+低丢包
50ms + 5%

sudo tc qdisc add dev lo root netem delay 50ms loss 5%
8.高延迟+高丢包
200ms + 20%

sudo tc qdisc add dev lo root netem delay 200ms loss 20%


每次测试前都先删旧规则：

sudo tc qdisc del dev lo root netem