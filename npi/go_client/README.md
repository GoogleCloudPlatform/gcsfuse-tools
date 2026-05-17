# custom-go-client-benchmark
To benchmark the read performance go-http client and go-grpc client.

## Execution steps:
1. Create the VM on which want to run the benchmark.
2. Access VM's terminal via SSH.
3. Install `git` and `go` if not installed already.
4. Clone this repo and make the cloned directory as working directory. 
5. Please change the object prefix and suffix - [here](https://github.com/raj-prince/custom-go-client-benchmark/blob/0db9e06666e71ac9e13972b61daa940f4cd6d5ef/main.go#L40)
6. Execute this command: `nohup ./execute_pb.sh <exp_number> > output.txt 2>&1 &`
7. The above command will generate two text files containing the latency for the
respective client.
8. Analyse the latency by various means, you may use python script to create
histogram from the above generated files.
```python
import sys
from matplotlib import pyplot as plt

print(sys.argv)

bins = []
for x in range(20, 100, 5):
    bins.append(x)

x = []
with open(sys.argv[1], 'r') as f:
    for line in f:
        x.append(float(line))

print("Average: ", (sum(x) / len(x)))

plt.hist(x, bins)

plt.show()

```

