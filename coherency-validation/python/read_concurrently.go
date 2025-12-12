package main

import (
	"bytes"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// O_DIRECT is a platform-specific flag (mainly Linux).
var O_DIRECT int = 0

// Define a common block size (4096 bytes) for aligned reading.
const ALIGNMENT_BLOCK_SIZE = 4096

func init() {
	// The syscall.O_DIRECT constant is only defined on Linux and some other Unix-like systems.
	if runtime.GOOS == "linux" {
		O_DIRECT = syscall.O_DIRECT
	}
}

func main() {
	// 1. Parse Flags
	sizeStrPtr := flag.String("size", "0", "Size of the file to create (e.g., 1024, 1K, 10M, 1G). If 0, uses existing file.")
	minReadSizeStrPtr := flag.String("min-read-size", "0", "Minimum block size for read operations per thread (e.g. 4K, 1M). Default is file-size/threads.")
	verifyPtr := flag.Bool("verify", false, "Verify the read content against the generated pattern.")
	threadsPtr := flag.Int("threads", 2, "Number of concurrent threads to use.")
	verbosePtr := flag.Bool("v", false, "Enable verbose logging.")
	quietPtr := flag.Bool("q", false, "Quiet mode: suppress non-error output.")
	directPtr := flag.Bool("direct", false, "Use O_DIRECT for reading.")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Usage: %s [options] <input_file>\n", os.Args[0])
		flag.PrintDefaults()
	}

	flag.Parse()

	if flag.NArg() < 1 {
		flag.Usage()
		os.Exit(1)
	}

	inputPath := flag.Arg(0)
	doVerify := *verifyPtr
	numThreads := *threadsPtr

	// Determine Quiet vs Verbose based on "latter wins" logic
	verbose := false
	quiet := false

	lastQ := -1
	lastV := -1

	for i, arg := range os.Args {
		if arg == "-q" || arg == "--q" {
			lastQ = i
		}
		if arg == "-v" || arg == "--v" {
			lastV = i
		}
	}

	if lastQ > lastV {
		quiet = true
		verbose = false
	} else if lastV > lastQ {
		verbose = true
		quiet = false
	} else {
		// Neither set, or defaults
		verbose = *verbosePtr
		quiet = *quietPtr
		if quiet {
			verbose = false
		}
	}

	useDirect := *directPtr

	if useDirect && !quiet {
		fmt.Println("O_DIRECT mode enabled.")
	}

	// Parse size strings
	targetSize, err := parseSize(*sizeStrPtr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing size '%s': %v\n", *sizeStrPtr, err)
		os.Exit(1)
	}

	minReadSizeArg, err := parseSize(*minReadSizeStrPtr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing min-read-size '%s': %v\n", *minReadSizeStrPtr, err)
		os.Exit(1)
	}

	// Variable to hold the expected data for verification
	var expectedContent []byte

	// 2. Handle File Creation / Setup
	if targetSize > 0 {
		if verbose {
			fmt.Printf("Generating %d bytes of data...\n", targetSize)
		}

		expectedContent = generateContent(targetSize)

		if err := os.WriteFile(inputPath, expectedContent, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "Error creating input file: %v\n", err)
			os.Exit(1)
		}
	} else {
		if doVerify {
			fmt.Println("Warning: --verify flag ignored because --size was not specified (cannot generate reference for existing file).")
			doVerify = false
		}
	}

	if verbose {
		fmt.Printf("Configuration:\n - Input: %s\n - Threads: %d\n", inputPath, numThreads)
		if targetSize > 0 {
			fmt.Printf(" - Mode: Created new file (%d bytes)\n", targetSize)
		} else {
			fmt.Printf(" - Mode: Reading existing file\n")
		}
		if useDirect {
			fmt.Printf(" - Mode: O_DIRECT enabled\n")
		}
	}

	// 3. Get Input File Size (Stat)
	fileInfo, err := os.Stat(inputPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error stating input file: %v\n", err)
		os.Exit(1)
	}
	fileSize := fileInfo.Size()

	// 4. Generate Random Ranges (Allow Overlaps)
	if numThreads < 1 {
		numThreads = 1
	}

	var ranges [][2]int64
	rand.Seed(time.Now().UnixNano())

	for i := 0; i < numThreads; i++ {
		if fileSize == 0 {
			ranges = append(ranges, [2]int64{0, 0})
			continue
		}

		// Random start
		start := rand.Int63n(fileSize)

		// Align start if needed
		if useDirect {
			start = (start / int64(ALIGNMENT_BLOCK_SIZE)) * int64(ALIGNMENT_BLOCK_SIZE)
		}

		// Random end (between start and fileSize)
		remaining := fileSize - start
		if remaining <= 0 {
			start = fileSize
			ranges = append(ranges, [2]int64{start, start})
			continue
		}

		// Random length
		length := rand.Int63n(remaining) + 1
		end := start + length

		ranges = append(ranges, [2]int64{start, end})
	}

	// Default minReadSize logic
	minReadSize := minReadSizeArg
	if minReadSize <= 0 {
		minReadSize = 1024 * 1024 // 1MB default chunk read size
	}

	if useDirect && minReadSize < ALIGNMENT_BLOCK_SIZE {
		minReadSize = ALIGNMENT_BLOCK_SIZE
	}

	var wg sync.WaitGroup
	var failureCount int32 // Atomic counter

	startTime := time.Now()

	// 5. Launch Threads
	for i, r := range ranges {
		wg.Add(1)
		go readChunk(inputPath, i, r[0], r[1], expectedContent, minReadSize, &wg, verbose, quiet, useDirect, &failureCount)
	}

	wg.Wait()
	duration := time.Since(startTime)

	if verbose {
		fmt.Printf("Reading complete in %v\n", duration)
	}

	if failureCount > 0 {
		os.Exit(1)
	}

	if doVerify && len(expectedContent) > 0 && failureCount == 0 {
		if verbose {
			fmt.Println("SUCCESS: All threads verified content successfully.")
		}
	}
}

// parseSize parses a string size like "1K", "10M", "1G" into bytes
func parseSize(s string) (int64, error) {
	s = strings.TrimSpace(strings.ToUpper(s))
	if s == "" || s == "0" {
		return 0, nil
	}

	multiplier := int64(1)
	numStr := s

	if strings.HasSuffix(s, "K") {
		multiplier = 1024
		numStr = s[:len(s)-1]
	} else if strings.HasSuffix(s, "M") {
		multiplier = 1024 * 1024
		numStr = s[:len(s)-1]
	} else if strings.HasSuffix(s, "G") {
		multiplier = 1024 * 1024 * 1024
		numStr = s[:len(s)-1]
	} else if strings.HasSuffix(s, "T") {
		multiplier = 1024 * 1024 * 1024 * 1024
		numStr = s[:len(s)-1]
	}

	val, err := strconv.ParseInt(numStr, 10, 64)
	if err != nil {
		return 0, err
	}

	return val * multiplier, nil
}

func generateContent(size int64) []byte {
	buf := make([]byte, size)
	for i := int64(0); i < size; i++ {
		buf[i] = byte((i % 94) + 33)
	}
	return buf
}

// formatInt formats an integer with commas (e.g., 1000000 -> "1,000,000")
func formatInt(n int64) string {
	in := strconv.FormatInt(n, 10)
	numOfDigits := len(in)
	if n < 0 {
		numOfDigits--
	}
	numOfCommas := (numOfDigits - 1) / 3

	out := make([]byte, len(in)+numOfCommas)
	if n < 0 {
		in, out[0] = in[1:], '-'
	}

	for i, j, k := len(in)-1, len(out)-1, 0; ; i, j = i-1, j-1 {
		out[j] = in[i]
		if i == 0 {
			return string(out)
		}
		if k++; k == 3 {
			j, k = j-1, 0
			out[j] = ','
		}
	}
}

func readChunk(path string, threadID int, start int64, end int64, expectedContent []byte, minReadSize int64, wg *sync.WaitGroup, verbose bool, quiet bool, useDirect bool, failureCount *int32) {
	defer wg.Done()

	// Default: Show thread activity. Quiet: Hide it.
	if !quiet {
		fmt.Printf("Starting thread#%d to read [%s -> %s) ...\n", threadID, formatInt(start), formatInt(end))
	}

	openFlags := os.O_RDONLY
	if useDirect {
		if O_DIRECT == 0 {
			fmt.Fprintf(os.Stderr, "[Thread %d] Warning: O_DIRECT not supported on this platform.\n", threadID)
		} else {
			openFlags |= O_DIRECT
		}
	}

	f, err := os.OpenFile(path, openFlags, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[Thread %d] Error opening file: %v\n", threadID, err)
		atomicAdd(failureCount, 1)
		return
	}
	defer f.Close()

	if start >= end {
		// Empty range logic
		if !quiet {
			fmt.Printf("... Ended thread#%d (empty/invalid range)\n", threadID)
		}
		return
	}

	_, err = f.Seek(start, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[Thread %d] Seek error: %v\n", threadID, err)
		atomicAdd(failureCount, 1)
		return
	}

	totalBytesToRead := end - start
	var bytesReadSoFar int64 = 0

	// Allocation
	// If O_DIRECT, buffer size must be aligned. We'll read in aligned chunks.
	// If not, we can use minReadSize.
	bufSize := minReadSize
	if useDirect {
		// Round up bufSize to alignment
		if bufSize%ALIGNMENT_BLOCK_SIZE != 0 {
			bufSize = ((bufSize / ALIGNMENT_BLOCK_SIZE) + 1) * ALIGNMENT_BLOCK_SIZE
		}
	}

	buffer := make([]byte, bufSize)

	for bytesReadSoFar < totalBytesToRead {
		// Determine how much we WANT to read logically
		remaining := totalBytesToRead - bytesReadSoFar

		readRequestSize := bufSize

		if !useDirect {
			if remaining < int64(len(buffer)) {
				readRequestSize = remaining
			}
		}

		// Perform Read
		n, err := f.Read(buffer[:readRequestSize])

		if n > 0 {
			// Current absolute file offset
			currentAbsOffset := start + bytesReadSoFar

			if expectedContent != nil {
				// Check bounds
				if currentAbsOffset+int64(n) > int64(len(expectedContent)) {
					// Read past expected content size? (File grew?)
					// Only verify up to expected content len
					validLen := int64(len(expectedContent)) - currentAbsOffset
					if validLen > 0 {
						if !bytes.Equal(buffer[:validLen], expectedContent[currentAbsOffset:currentAbsOffset+validLen]) {
							fmt.Fprintf(os.Stderr, "[Thread %d] FAILURE: Mismatch at offset %d\n", threadID, currentAbsOffset)
							atomicAdd(failureCount, 1)
						}
					}
				} else {
					if !bytes.Equal(buffer[:n], expectedContent[currentAbsOffset:currentAbsOffset+int64(n)]) {
						fmt.Fprintf(os.Stderr, "[Thread %d] FAILURE: Mismatch at offset %d\n", threadID, currentAbsOffset)
						atomicAdd(failureCount, 1)
					}
				}
			}

			bytesReadSoFar += int64(n)
		}

		if err != nil {
			if err == io.EOF {
				break
			}
			fmt.Fprintf(os.Stderr, "[Thread %d] Read error: %v\n", threadID, err)
			atomicAdd(failureCount, 1)
			break
		}

		if bytesReadSoFar >= totalBytesToRead {
			break
		}
	}

	if !quiet {
		fmt.Printf("... Ended thread#%d\n", threadID)
	}
}

func atomicAdd(addr *int32, delta int32) {
	atomic.AddInt32(addr, delta)
}
