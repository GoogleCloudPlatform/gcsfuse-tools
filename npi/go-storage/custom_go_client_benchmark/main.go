package main

import (
	"context"
	"crypto/tls"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"sync/atomic"
	"time"

	"cloud.google.com/go/storage"
	"github.com/googleapis/gax-go/v2"
	"golang.org/x/oauth2"
	"golang.org/x/sync/errgroup"
	"google.golang.org/api/option"
)

var (
	maxConnsPerHost     = 0
	maxIdleConnsPerHost = 100

	// MiB means 1024 KiB.
	MiB = int64(1024 * 1024)

	numOfWorkers = flag.Int("worker", 128, "Number of concurrent worker to read")

	runTime = flag.Duration("run-time", 5*time.Minute, "Actual workload runtime")

	warmUpTime = flag.Duration("warm-up-time", 2*time.Minute, "Ramp up time")

	grpcConnPoolSize = flag.Int("grpc-conn-pool-size", 1, "grpc connection pool size")

	maxRetryDuration = 30 * time.Second

	retryMultiplier = 2.0

	bucketName = flag.String("bucket", "kislayk_europe_west4", "GCS bucket name.")

	clientProtocol = flag.String("client-protocol", "http", "Network protocol.")

	// Object name = objectNamePrefix + {thread_id} + objectNameSuffix
	objectNamePrefix = flag.String("obj-prefix", "1GB/experiment.", "Object prefix")
	objectNameSuffix = flag.String("obj-suffix", ".0", "Object suffix")
)

// CreateHTTPClient create http storage client.
func CreateHTTPClient(ctx context.Context) (client *storage.Client, err error) {
	var transport *http.Transport
	// Using http1 makes the client more performant.
	transport = &http.Transport{
		MaxConnsPerHost:     maxConnsPerHost,
		MaxIdleConnsPerHost: maxIdleConnsPerHost,
		// This disables HTTP/2 in transport.
		TLSNextProto: make(
			map[string]func(string, *tls.Conn) http.RoundTripper,
		),
	}

	tokenSource, err := GetTokenSource(ctx, "")
	if err != nil {
		return nil, fmt.Errorf("while generating tokenSource, %v", err)
	}

	// Custom http client for Go Client.
	httpClient := &http.Client{
		Transport: &userAgentRoundTripper{
			wrapped: &oauth2.Transport{
				Base:   transport,
				Source: tokenSource,
			},
			UserAgent: "prince",
		},
		Timeout: 0,
	}
	return storage.NewClient(ctx, option.WithHTTPClient(httpClient))
}

func rampUp(warmupCtx context.Context, cancelFn context.CancelFunc, bucketHandle *storage.BucketHandle) {
	idx := 0
	var eG errgroup.Group
	for {
		select {
		case <-warmupCtx.Done():
			cancelFn()
			return
		default:
			if idx == *numOfWorkers {
				eG.Wait()
				return
			}
			time.Sleep(1 * time.Second)
			eG.Go(func() error {
				_, err := ReadObject(warmupCtx, idx, bucketHandle)
				if err != nil {
					err = fmt.Errorf("while reading object %v: %w", *objectNamePrefix+strconv.Itoa(idx)+*objectNameSuffix, err)
					return err
				}
				return err
			})
			idx++
		}
	}
}

// CreateGrpcClient creates grpc client.
func CreateGrpcClient(ctx context.Context) (client *storage.Client, err error) {
	tokenSource, err := GetTokenSource(ctx, "")
	if err != nil {
		return nil, err
	}
	return storage.NewGRPCClient(ctx, option.WithGRPCConnectionPool(*grpcConnPoolSize), option.WithTokenSource(tokenSource), storage.WithDisabledClientMetrics())
}

// ReadObject creates reader object corresponding to workerID with the help of bucketHandle.
func ReadObject(ctx context.Context, workerID int, bucketHandle *storage.BucketHandle) (bytesRead int64, err error) {
	objectName := *objectNamePrefix + strconv.Itoa(workerID) + *objectNameSuffix

	select {
	case <-ctx.Done():
		return 0, nil
	default:
		object := bucketHandle.Object(objectName)
		rc, err := object.NewReader(ctx)
		if err != nil {
			return 0, fmt.Errorf("while creating reader object: %v", err)
		}
		defer rc.Close()

		// Calls Reader.WriteTo implicitly.
		count, err := io.Copy(io.Discard, rc)
		bytesRead += count
		if err != nil {
			return bytesRead, fmt.Errorf("while reading and discarding content: %v", err)
		}
		return bytesRead, nil
	}
}

func main() {
	flag.Parse()
	fmt.Printf("Workload start time: %s\n", time.Now().String())
	ctx := context.Background()

	var client *storage.Client
	var err error
	protocol := ""
	if *clientProtocol == "http" {
		protocol = "http"
		client, err = CreateHTTPClient(ctx)
	} else {
		protocol = "grpc"
		client, err = CreateGrpcClient(ctx)
	}

	if err != nil {
		fmt.Printf("while creating the client: %v", err)
		os.Exit(1)
	}

	client.SetRetry(
		storage.WithBackoff(gax.Backoff{
			Max:        maxRetryDuration,
			Multiplier: retryMultiplier,
		}),
		storage.WithPolicy(storage.RetryAlways))

	// assumes bucket already exist
	bucketHandle := client.Bucket(*bucketName)

	var totalBytesRead atomic.Int64

	warmupCtx, cancelFn := context.WithDeadline(ctx, time.Now().Add(*warmUpTime))
	//fmt.Println("Ramp-up starts")

	rampUp(warmupCtx, cancelFn, bucketHandle)

	//fmt.Println("Ramp-up complete. Starting run on actual traffic.")
	startTime := time.Now()
	var eG errgroup.Group

	actualRunCtx, cancelFn := context.WithDeadline(ctx, startTime.Add(*runTime))
	defer cancelFn()

	// Run the actual workload
	for i := 0; i < *numOfWorkers; i++ {
		idx := i
		eG.Go(func() error {
			//fmt.Printf("Worker %d started\n", idx)
			for {
				select {
				case <-actualRunCtx.Done():
					return nil
				default:
					bytesRead, _ := ReadObject(actualRunCtx, idx, bucketHandle)
					totalBytesRead.Add(bytesRead)
				}
			}
		})
	}

	err = eG.Wait()
	totalDuration := time.Since(startTime)

	if err == nil && err != context.DeadlineExceeded {
		fmt.Printf("Protocol: %s, Bandwidth: %d MiB/s\n", protocol, totalBytesRead.Load()/(int64(totalDuration.Seconds())*MiB))
		fmt.Printf("Workload end time: %s\n\n", time.Now().String())
		os.Exit(0)
	} else {
		fmt.Fprintf(os.Stderr, "Error while running benchmark: %v", err)
		fmt.Printf("Workload end time: %s\n\n", time.Now().String())
		os.Exit(1)
	}
}
