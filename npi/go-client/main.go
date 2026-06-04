package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"cloud.google.com/go/storage"
	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
	"google.golang.org/api/option"
)

var (
	bucketName       = flag.String("bucket", "", "GCS bucket name.")
	clientProtocol   = flag.String("client-protocol", "http", "Network protocol: http or grpc.")
	blockSizeStr     = flag.String("bs", "1M", "Block size (e.g. 128K, 1M, etc.).")
	fileSizeStr      = flag.String("filesize", "1G", "File size per file (e.g. 1M, 10M, 1G).")
	numOfWorkers     = flag.Int("numjobs", 128, "Number of concurrent workers (threads) to read.")
	nrFiles          = flag.Int("nrfiles", 10, "How many files does each thread/worker need to read.")
	objectNamePrefix = flag.String("obj-prefix", "", "Prefix for GCS objects.")
	grpcConnPoolSize = flag.Int("grpc-conn-pool-size", 1, "gRPC connection pool size.")
)

type ZeroReader struct{}

func (ZeroReader) Read(p []byte) (int, error) {
	clear(p)
	return len(p), nil
}

func parseSize(s string) (int64, error) {
	if len(s) == 0 {
		return 0, fmt.Errorf("empty size string")
	}
	unit := s[len(s)-1]
	valStr := s[:len(s)-1]
	val, err := strconv.ParseInt(valStr, 10, 64)
	if err != nil {
		val, err = strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0, err
		}
		return val, nil
	}
	switch unit {
	case 'k', 'K':
		return val * 1024, nil
	case 'm', 'M':
		return val * 1024 * 1024, nil
	case 'g', 'G':
		return val * 1024 * 1024 * 1024, nil
	default:
		val, err = strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid size format: %s", s)
		}
		return val, nil
	}
}

func CreateHTTPClient(ctx context.Context) (*storage.Client, error) {
	transport := &http.Transport{
		MaxConnsPerHost:     1000,
		MaxIdleConnsPerHost: 1000,
		TLSNextProto:        make(map[string]func(string, *tls.Conn) http.RoundTripper),
	}

	tokenSource, err := google.DefaultTokenSource(ctx, storage.ScopeFullControl)
	if err != nil {
		return nil, fmt.Errorf("failed to get default token source: %w", err)
	}

	httpClient := &http.Client{
		Transport: &oauth2.Transport{
			Base:   transport,
			Source: tokenSource,
		},
		Timeout: 0,
	}
	return storage.NewClient(ctx, option.WithHTTPClient(httpClient))
}

func CreateGrpcClient(ctx context.Context) (*storage.Client, error) {
	tokenSource, err := google.DefaultTokenSource(ctx, storage.ScopeFullControl)
	if err != nil {
		return nil, fmt.Errorf("failed to get default token source: %w", err)
	}
	return storage.NewGRPCClient(
		ctx,
		option.WithGRPCConnectionPool(*grpcConnPoolSize),
		option.WithTokenSource(tokenSource),
		storage.WithDisabledClientMetrics(),
	)
}

func getObjectPath(workerID, fileIndex int) string {
	// e.g. go-benchmark/read/1G/10/experiment.0.1
	return fmt.Sprintf("%sread/%s/%d/experiment.%d.%d",
		*objectNamePrefix, *fileSizeStr, *nrFiles, workerID, fileIndex)
}

func populateFilesIfMissing(ctx context.Context, client *storage.Client, bucketName string, fileSize int64) error {
	bucket := client.Bucket(bucketName)
	var wg sync.WaitGroup
	semaphore := make(chan struct{}, 50) // limit concurrency to 50
	errChan := make(chan error, *numOfWorkers**nrFiles)

	fmt.Fprintf(os.Stderr, "Checking and preparing benchmark files in GCS bucket gs://%s...\n", bucketName)

	for w := 0; w < *numOfWorkers; w++ {
		for f := 0; f < *nrFiles; f++ {
			workerID := w
			fileIndex := f

			wg.Add(1)
			go func() {
				defer wg.Done()
				select {
				case <-ctx.Done():
					return
				case semaphore <- struct{}{}:
					defer func() { <-semaphore }()
				}

				objName := getObjectPath(workerID, fileIndex)
				obj := bucket.Object(objName)
				attrs, err := obj.Attrs(ctx)
				if err == nil {
					if attrs.Size == fileSize {
						// File already exists and has correct size, skip upload
						return
					}
				} else if !errors.Is(err, storage.ErrObjectNotExist) {
					errChan <- fmt.Errorf("failed to check status of %s: %w", objName, err)
					return
				}

				// Upload file
				fmt.Fprintf(os.Stderr, "Creating gs://%s/%s (%s)...\n", bucketName, objName, *fileSizeStr)
				wc := obj.NewWriter(ctx)
				src := io.LimitReader(ZeroReader{}, fileSize)
				if _, err := io.Copy(wc, src); err != nil {
					errChan <- fmt.Errorf("failed to write %s: %w", objName, err)
					wc.Close()
					return
				}
				if err := wc.Close(); err != nil {
					errChan <- fmt.Errorf("failed to close %s: %w", objName, err)
				}
			}()
		}
	}

	wg.Wait()
	close(errChan)

	for err := range errChan {
		if err != nil {
			return err
		}
	}
	fmt.Fprintln(os.Stderr, "All benchmark files prepared successfully.")
	return nil
}

type LatencyStats struct {
	Mean int64            `json:"mean"`
	P99  int64            `json:"99.000000"`
	P995 int64            `json:"99.500000"`
	P999 int64            `json:"99.900000"`
	Percentiles map[string]int64 `json:"percentiles"`
}

type ReadOpStats struct {
	Bw          float64      `json:"bw"` // KiB/s
	Iops        float64      `json:"iops"`
	LatNs       LatencyStats `json:"lat_ns"`
}

type JobResult struct {
	JobName    string            `json:"jobname"`
	JobOptions map[string]string `json:"job options"`
	ReadStats  ReadOpStats       `json:"read"`
}

type OutputSchema struct {
	GlobalOptions map[string]string `json:"global options"`
	Jobs          []JobResult       `json:"jobs"`
}

func main() {
	flag.Parse()

	if *bucketName == "" {
		fmt.Fprintln(os.Stderr, "Error: --bucket flag is required")
		os.Exit(1)
	}

	if *numOfWorkers <= 0 {
		fmt.Fprintln(os.Stderr, "Error: --numjobs must be greater than 0")
		os.Exit(1)
	}

	if *nrFiles <= 0 {
		fmt.Fprintln(os.Stderr, "Error: --nrfiles must be greater than 0")
		os.Exit(1)
	}

	ctx := context.Background()
	var client *storage.Client
	var err error

	if *clientProtocol == "http" {
		client, err = CreateHTTPClient(ctx)
	} else if *clientProtocol == "grpc" {
		client, err = CreateGrpcClient(ctx)
	} else {
		fmt.Fprintf(os.Stderr, "Error: invalid client-protocol: %s\n", *clientProtocol)
		os.Exit(1)
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "Error creating storage client: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()

	fileSize, err := parseSize(*fileSizeStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing filesize: %v\n", err)
		os.Exit(1)
	}

	blockSize, err := parseSize(*blockSizeStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing bs (block size): %v\n", err)
		os.Exit(1)
	}

	// 1. Ensure files exist
	if err := populateFilesIfMissing(ctx, client, *bucketName, fileSize); err != nil {
		fmt.Fprintf(os.Stderr, "Error preparing benchmark files: %v\n", err)
		os.Exit(1)
	}

	// 2. Perform the benchmark
	bucket := client.Bucket(*bucketName)
	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	var totalBytesRead int64
	var totalIOOperations int64

	// Slice of latency list for each worker to avoid synchronization overhead
	workerLatencies := make([][]time.Duration, *numOfWorkers)
	for i := range workerLatencies {
		workerLatencies[i] = make([]time.Duration, 0, 1000)
	}

	var wg sync.WaitGroup

	fmt.Fprintln(os.Stderr, "Starting benchmark read phase...")
	startTime := time.Now()

	for w := 0; w < *numOfWorkers; w++ {
		workerID := w
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, blockSize)

			for f := 0; f < *nrFiles; f++ {
				// Check for cancellation before starting next file
				select {
				case <-runCtx.Done():
					return
				default:
				}

				objName := getObjectPath(workerID, f)
				obj := bucket.Object(objName)

				rc, err := obj.NewReader(runCtx)
				if err != nil {
					// If context is cancelled, this error is expected
					if runCtx.Err() == nil {
						fmt.Fprintf(os.Stderr, "Worker %d failed to open reader: %v\n", workerID, err)
						os.Exit(1)
					}
					return
				}

				for {
					select {
					case <-runCtx.Done():
						rc.Close()
						return
					default:
					}

					ioStart := time.Now()
					n, err := rc.Read(buf)
					ioDuration := time.Since(ioStart)

					if n > 0 {
						atomic.AddInt64(&totalBytesRead, int64(n))
						atomic.AddInt64(&totalIOOperations, 1)
						workerLatencies[workerID] = append(workerLatencies[workerID], ioDuration)
					}

					if err != nil {
						if err == io.EOF {
							break
						}
						if runCtx.Err() == nil {
							fmt.Fprintf(os.Stderr, "Worker %d error reading: %v\n", workerID, err)
							rc.Close()
							os.Exit(1)
						}
						rc.Close()
						return
					}
				}
				rc.Close()
			}

			// If we reached here, this worker has completed reading all its files!
			// We cancel the context to signal all other workers to stop immediately (similar to exitall=1).
			cancel()
		}()
	}

	wg.Wait()
	elapsed := time.Since(startTime)
	fmt.Fprintf(os.Stderr, "Benchmark read phase completed in %v.\n", elapsed)

	// Combine and sort latencies
	var allLatencies []time.Duration
	for _, wl := range workerLatencies {
		allLatencies = append(allLatencies, wl...)
	}

	sort.Slice(allLatencies, func(i, j int) bool {
		return allLatencies[i] < allLatencies[j]
	})

	var meanLatNs int64
	var p99LatNs int64
	var p995LatNs int64
	var p999LatNs int64

	totalIOs := int64(len(allLatencies))
	if totalIOs > 0 {
		var sum int64
		for _, lat := range allLatencies {
			sum += lat.Nanoseconds()
		}
		meanLatNs = sum / totalIOs
		p99Idx := int(float64(totalIOs) * 0.99)
		p995Idx := int(float64(totalIOs) * 0.995)
		p999Idx := int(float64(totalIOs) * 0.999)
		if p99Idx >= int(totalIOs) {
			p99Idx = int(totalIOs) - 1
		}
		if p995Idx >= int(totalIOs) {
			p995Idx = int(totalIOs) - 1
		}
		if p999Idx >= int(totalIOs) {
			p999Idx = int(totalIOs) - 1
		}
		p99LatNs = allLatencies[p99Idx].Nanoseconds()
		p995LatNs = allLatencies[p995Idx].Nanoseconds()
		p999LatNs = allLatencies[p999Idx].Nanoseconds()
	}

	bwKiBps := (float64(totalBytesRead) / 1024.0) / elapsed.Seconds()
	iops := float64(totalIOOperations) / elapsed.Seconds()

	// Format output to JSON matching FIO runner expected format
	output := OutputSchema{
		GlobalOptions: map[string]string{
			"iodepth": "1",
			"rw":      "read",
		},
		Jobs: []JobResult{
			{
				JobName: "go-client-read",
				JobOptions: map[string]string{
					"bs":              *blockSizeStr,
					"filesize":        *fileSizeStr,
					"nrfiles":         strconv.Itoa(*nrFiles),
					"numjobs":         strconv.Itoa(*numOfWorkers),
					"client_protocol": *clientProtocol,
				},
				ReadStats: ReadOpStats{
					Bw:   bwKiBps,
					Iops: iops,
					LatNs: LatencyStats{
						Mean: meanLatNs,
						P99:  p99LatNs,
						P995: p995LatNs,
						P999: p999LatNs,
						Percentiles: map[string]int64{
							"99.000000":  p99LatNs,
							"99.500000":  p995LatNs,
							"99.900000":  p999LatNs,
						},
					},
				},
			},
		},
	}

	jsonBytes, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error marshalling output: %v\n", err)
		os.Exit(1)
	}

	fmt.Println(string(jsonBytes))
}
