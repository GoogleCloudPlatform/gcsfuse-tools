package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
)

// O_DIRECT is a platform-specific flag (mainly Linux) for Direct I/O.
var O_DIRECT int = 0

// Define a common block size (4096 bytes) for aligned writing.
const ALIGNMENT_BLOCK_SIZE = 4096

const DEFAULT_CONTENT = "sample content"

func init() {
	// O_DIRECT is defined only on Linux and some other Unix-like systems.
	if runtime.GOOS == "linux" {
		O_DIRECT = syscall.O_DIRECT
	}
}

// parseSize parses a string size like "1K", "10M", "1G" into bytes.
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

// generateContent creates a deterministic byte pattern of the given size.
func generateContent(size int64) []byte {
	buf := make([]byte, size)
	for i := int64(0); i < size; i++ {
		// Simple pattern: rotate through printable ASCII or just bytes
		buf[i] = byte((i % 94) + 33)
	}
	return buf
}

// writeDirectAligned pads the content to the next ALIGNMENT_BLOCK_SIZE multiple
// and writes the entire padded buffer. This satisfies O_DIRECT length alignment.
func writeDirectAligned(f *os.File, data []byte) (int, error) {
	dataLen := len(data)

	// Calculate the total size needed (next multiple of ALIGNMENT_BLOCK_SIZE)
	// Example: (5 + 4096 - 1) / 4096 * 4096 = 1 * 4096 = 4096
	paddedSize := (dataLen + ALIGNMENT_BLOCK_SIZE - 1) / ALIGNMENT_BLOCK_SIZE * ALIGNMENT_BLOCK_SIZE

	// Create the padded buffer
	paddedData := make([]byte, paddedSize)
	copy(paddedData, data) // Copy the actual content

	// Write the padded buffer. The write size is now aligned.
	n, err := f.Write(paddedData)

	if err == nil {
		fmt.Printf("Note: Wrote %d padded bytes, containing %d bytes of actual content.\n", n, dataLen)
	}

	return n, err
}

func main() {
	// 1. Define Command Line Flags
	contentFlag := flag.String("content", DEFAULT_CONTENT, "The string content to write to the file.")
	sizeFlag := flag.String("size", "0", "Size of the file to create (e.g., 1024, 1K, 10M, 1G). If set, ignores content default.")
	noSyncFlag := flag.Bool("no-sync", false, "If true, skips calling file.Sync() to persist data to physical storage.")
	noFlushFlag := flag.Bool("no-flush", false, "If true, skips calling file.Close(), leaving the file handle open on exit (skips final kernel buffer flush).")
	directFlag := flag.Bool("direct", false, "If true, attempts to open the file with O_DIRECT for writing (platform-specific).")
	duplicateWritesFlag := flag.Int("duplicate-writes", 1, "Number of concurrent write threads to duplicate the write operation.")

	flag.Parse()

	// 2. Validate File Path
	if flag.NArg() < 1 {
		fmt.Println("Error: Missing file path argument.")
		fmt.Println("Usage: go run write.go [OPTIONS] <file-path>")
		flag.PrintDefaults()
		os.Exit(1)
	}

	filePath := flag.Arg(0)

	// Check which flags were explicitly set
	isContentSet := false
	isSizeSet := false
	flag.Visit(func(f *flag.Flag) {
		if f.Name == "content" {
			isContentSet = true
		}
		if f.Name == "size" {
			isSizeSet = true
		}
	})

	if isContentSet && isSizeSet {
		fmt.Fprintln(os.Stderr, "Error: Cannot specify both --content and --size.")
		os.Exit(1)
	}

	// Determine data source
	var data []byte

	targetSize, err := parseSize(*sizeFlag)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing size '%s': %v\n", *sizeFlag, err)
		os.Exit(1)
	}

	if targetSize > 0 {
		// Size mode
		data = generateContent(targetSize)
	} else {
		// Content mode (default or explicit)
		data = []byte(*contentFlag)
	}

	// 3. Determine File Open Flags
	// Start with flags for Write-Only, Create if not exists, and Truncate (overwrite)
	openFlags := os.O_WRONLY | os.O_CREATE | os.O_TRUNC
	isDirect := *directFlag

	if isDirect {
		if O_DIRECT == 0 {
			isDirect = false // Fallback
			fmt.Fprintf(os.Stderr, "Warning: --direct flag used, but O_DIRECT is not supported on %s. Using standard flags.\n", runtime.GOOS)
		} else {
			// Combine standard write flags with O_DIRECT
			openFlags |= O_DIRECT
			fmt.Printf("Attempting to open file '%s' with O_DIRECT (using aligned write padding).\n", filePath)
		}
	} else {
		fmt.Printf("Opening file '%s' with standard write flags.\n", filePath)
	}

	numWrites := *duplicateWritesFlag
	if numWrites < 1 {
		numWrites = 1
	}

	var wg sync.WaitGroup
	wg.Add(numWrites)

	var errorCount int32 = 0

	fmt.Printf("Starting %d concurrent write(s) to '%s'\n", numWrites, filePath)

	for i := 0; i < numWrites; i++ {
		go func(id int) {
			defer wg.Done()

			// 4. Open the file
			// Permission 0666 grants read/write to owner, group, and others (standard file permission).
			f, err := os.OpenFile(filePath, openFlags, 0666)
			if err != nil {
				fmt.Fprintf(os.Stderr, "[Thread %d] Error opening file '%s': %v\n", id, filePath, err)
				atomic.AddInt32(&errorCount, 1)
				return
			}

			// 5. Write Content
			var n int
			var writeErr error

			if isDirect {
				// Use the aligned write function for O_DIRECT
				n, writeErr = writeDirectAligned(f, data)
			} else {
				// Use standard write for non-direct operations
				n, writeErr = f.Write(data)
			}

			if writeErr != nil {
				fmt.Fprintf(os.Stderr, "[Thread %d] Error writing content: %v\n", id, writeErr)
				atomic.AddInt32(&errorCount, 1)
				// Try to close the file even on write error, unless no-flush is set (though strictly on error we might want to close anyway)
				if !*noFlushFlag {
					f.Close()
				}
				return
			}

			// If not in direct mode, or if direct mode succeeded, print the byte count normally.
			if !isDirect {
				fmt.Printf("[Thread %d] Wrote %d bytes to file.\n", id, n)
			}

			// 6. Sync/Close Control Logic

			// Sync Control
			if !*noSyncFlag {
				// fmt.Printf("[Thread %d] Action: Calling file.Sync()\n", id)
				if err := f.Sync(); err != nil {
					fmt.Fprintf(os.Stderr, "[Thread %d] Error during file.Sync(): %v\n", id, err)
					atomic.AddInt32(&errorCount, 1)
					// Continue, but note the error
				}
			}

			// Flush/Close Control
			if !*noFlushFlag {
				// file.Close() ensures any remaining kernel buffers are flushed and closes the descriptor.
				// fmt.Printf("[Thread %d] Action: Calling file.Close()\n", id)
				if err := f.Close(); err != nil {
					fmt.Fprintf(os.Stderr, "[Thread %d] Error during file.Close(): %v\n", id, err)
					atomic.AddInt32(&errorCount, 1)
					return
				}
				// fmt.Printf("[Thread %d] Write process completed and file handle closed.\n", id)
			} else {
				// If --no-flush is set, skip file.Close() and block the program from terminating.
				fmt.Printf("[Thread %d] Action: Skipping file.Close() (--no-flush is true). File handle remains OPEN.\n", id)
			}
		}(i)
	}

	// Wait for all goroutines to finish their write/sync operations
	wg.Wait()
	fmt.Println("All write operations completed.")

	if errorCount > 0 {
		fmt.Fprintf(os.Stderr, "FAILURE: %d write operation(s) failed.\n", errorCount)
		os.Exit(1)
	}

	if *noFlushFlag {
		fmt.Println("--------------------------------------------------------------------------------")
		fmt.Println(">> Program is intentionally BLOCKING to keep the file descriptors open.")
		fmt.Println(">> The kernel buffers will remain unflushed until you manually interrupt (Ctrl+C).")
		fmt.Println("--------------------------------------------------------------------------------")

		// Create a channel to listen for OS signals (like Ctrl+C / SIGINT)
		stopChan := make(chan os.Signal, 1)
		signal.Notify(stopChan, os.Interrupt, syscall.SIGTERM)

		fmt.Println(">> Waiting for interrupt signal (Ctrl+C) to exit...")

		// Block the main goroutine indefinitely until a signal is received
		<-stopChan

		// When Ctrl+C is pressed, the program receives the signal and exits naturally,
		// allowing the OS to clean up the file descriptor (which will flush the data).
		fmt.Println("\nInterrupt signal received. Exiting now.")
	}
}
