package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"sync"
	"syscall"
	"time"

	"cloud.google.com/go/storage"
	"cloud.google.com/go/storage/experimental"
	"google.golang.org/api/option"
	"google.golang.org/grpc"

	_ "google.golang.org/grpc/balancer/rls"
	_ "google.golang.org/grpc/xds/googledirectpath"
)

var (
	bucketName       = flag.String("bucket", "", "GCS bucket name.")
	fileSizeStr      = flag.String("filesize", "1M", "File size per file (e.g. 1M, 10M).")
	nrFiles          = flag.Int("nrfiles", 1, "Number of files to create and read.")
	objectNamePrefix = flag.String("obj-prefix", "go-grpc-verify/", "Prefix for GCS objects.")
)

var (
	connMu    sync.Mutex
	connAddrs []string
	connMSSs  []int
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

func customDialer(ctx context.Context, addr string) (net.Conn, error) {
	var d net.Dialer
	conn, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, err
	}

	tcpConn, ok := conn.(*net.TCPConn)
	if !ok {
		return conn, nil
	}

	rawConn, err := tcpConn.SyscallConn()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to get SyscallConn: %v\n", err)
		return conn, nil
	}

	var mss int
	var controlErr error
	err = rawConn.Control(func(fd uintptr) {
		mss, err = syscall.GetsockoptInt(int(fd), syscall.IPPROTO_TCP, syscall.TCP_MAXSEG)
		if err != nil {
			controlErr = err
		}
	})

	connMu.Lock()
	defer connMu.Unlock()

	remoteAddr := conn.RemoteAddr().String()
	connAddrs = append(connAddrs, remoteAddr)

	if err == nil && controlErr == nil {
		connMSSs = append(connMSSs, mss)
		fmt.Fprintf(os.Stderr, "Established TCP connection to %s, negotiated MSS: %d\n", remoteAddr, mss)
	} else {
		fmt.Fprintf(os.Stderr, "Established TCP connection to %s (could not read MSS: %v)\n", remoteAddr, controlErr)
	}

	return conn, nil
}

func isDirectPathIP(ip net.IP) bool {
	if ip == nil {
		return false
	}
	// DirectPath IPs are typically IPv6 and belong to Google's range,
	// commonly 2001:4860:8040::/42 or similar.
	_, directpathNet, err := net.ParseCIDR("2001:4860:8040::/42")
	if err == nil && directpathNet.Contains(ip) {
		return true
	}
	return false
}

func checkDirectPath() {
	connMu.Lock()
	defer connMu.Unlock()

	fmt.Printf("\n=== Connection Analysis ===\n")
	fmt.Printf("Total active/established connections: %d\n", len(connAddrs))

	hasIPv6 := false
	hasDirectPath := false

	for i, addrStr := range connAddrs {
		host, _, err := net.SplitHostPort(addrStr)
		if err != nil {
			host = addrStr
		}
		ip := net.ParseIP(host)
		isIPv6 := ip != nil && ip.To4() == nil

		isDP := isDirectPathIP(ip)
		if isIPv6 {
			hasIPv6 = true
		}
		if isDP {
			hasDirectPath = true
		}

		mssStr := "unknown"
		if i < len(connMSSs) {
			mssStr = fmt.Sprintf("%d", connMSSs[i])
		}

		fmt.Printf("- Conn #%d: Address=%s, IPv6=%t, DirectPathRange=%t, MSS=%s\n",
			i+1, addrStr, isIPv6, isDP, mssStr)
	}

	fmt.Printf("Summary: IPv6 Connections Detected: %t\n", hasIPv6)
	fmt.Printf("Summary: DirectPath IP Range Detected: %t\n", hasDirectPath)
	if hasDirectPath {
		fmt.Printf("RESULT: DirectPath is verified as ACTIVE!\n")
	} else if hasIPv6 {
		fmt.Printf("RESULT: DirectPath is likely ACTIVE (IPv6 connection present)!\n")
	} else {
		fmt.Printf("RESULT: DirectPath is NOT active (No IPv6/DirectPath connections found)!\n")
	}
	fmt.Printf("===========================\n\n")
}

func getObjectPath(fileIndex int) string {
	return fmt.Sprintf("%sexperiment.%d", *objectNamePrefix, fileIndex)
}

func populateFilesIfMissing(ctx context.Context, client *storage.Client, bucketName string, fileSize int64) error {
	bucket := client.Bucket(bucketName)

	for f := 0; f < *nrFiles; f++ {
		objName := getObjectPath(f)
		obj := bucket.Object(objName)
		attrs, err := obj.Attrs(ctx)
		if err == nil {
			if attrs.Size == fileSize {
				fmt.Fprintf(os.Stderr, "File gs://%s/%s already exists and is correct size, skipping creation.\n", bucketName, objName)
				continue
			}
		} else if !errors.Is(err, storage.ErrObjectNotExist) {
			return fmt.Errorf("failed to check status of %s: %w", objName, err)
		}

		fmt.Fprintf(os.Stderr, "Creating gs://%s/%s (%s)...\n", bucketName, objName, *fileSizeStr)
		wc := obj.NewWriter(ctx)
		src := io.LimitReader(ZeroReader{}, fileSize)
		if _, err := io.Copy(wc, src); err != nil {
			wc.Close()
			return fmt.Errorf("failed to write %s: %w", objName, err)
		}
		if err := wc.Close(); err != nil {
			return fmt.Errorf("failed to close %s: %w", objName, err)
		}
	}
	return nil
}

func main() {
	flag.Parse()

	if *bucketName == "" {
		fmt.Fprintln(os.Stderr, "Error: --bucket flag is required")
		os.Exit(1)
	}

	fileSize, err := parseSize(*fileSizeStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing filesize: %v\n", err)
		os.Exit(1)
	}

	ctx := context.Background()

	// Enable DirectPath via environment variable
	if err := os.Setenv("GOOGLE_CLOUD_ENABLE_DIRECT_PATH_XDS", "true"); err != nil {
		fmt.Fprintf(os.Stderr, "Error setting direct path env var: %v\n", err)
		os.Exit(1)
	}

	// Instantiate the Storage gRPC Client with the custom dialer and DirectPath enforced
	fmt.Fprintln(os.Stderr, "Initializing GCS gRPC Client...")
	client, err := storage.NewGRPCClient(
		ctx,
		option.WithGRPCDialOption(grpc.WithContextDialer(customDialer)),
		storage.WithDisabledClientMetrics(),
		experimental.WithDirectConnectivityEnforced(),
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error creating storage client: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()

	// 1. Populate test files
	if err := populateFilesIfMissing(ctx, client, *bucketName, fileSize); err != nil {
		fmt.Fprintf(os.Stderr, "Error preparing files: %v\n", err)
		os.Exit(1)
	}

	// 2. Download files
	bucket := client.Bucket(*bucketName)
	buf := make([]byte, 1024*1024) // 1MB buffer

	fmt.Fprintln(os.Stderr, "Downloading files...")
	for f := 0; f < *nrFiles; f++ {
		objName := getObjectPath(f)
		obj := bucket.Object(objName)

		start := time.Now()
		rc, err := obj.NewReader(ctx)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error opening reader for %s: %v\n", objName, err)
			os.Exit(1)
		}

		var totalBytes int64
		for {
			n, err := rc.Read(buf)
			totalBytes += int64(n)
			if err != nil {
				if err == io.EOF {
					break
				}
				fmt.Fprintf(os.Stderr, "Error reading %s: %v\n", objName, err)
				rc.Close()
				os.Exit(1)
			}
		}
		rc.Close()
		fmt.Fprintf(os.Stderr, "Downloaded %s (%d bytes) in %v\n", objName, totalBytes, time.Since(start))
	}

	// 3. Connection and DirectPath verification
	checkDirectPath()
}
