package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"runtime"
	"syscall"
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

// readDirectAligned reads the file content in block-aligned chunks, which is required by O_DIRECT.
// This function replaces the non-aligned io.ReadAll().
func readDirectAligned(f *os.File) ([]byte, error) {
	// Create a buffer that will hold the final file content
	var content []byte

	// Use a buffer size that is guaranteed to be aligned to the block size (e.g., 4096).
	buffer := make([]byte, ALIGNMENT_BLOCK_SIZE)

	for {
		// Read a block-aligned chunk. The syscalls beneath the Read method will
		// perform aligned reads of ALIGNMENT_BLOCK_SIZE bytes.
		n, err := f.Read(buffer)

		// Append the data read to the final content slice
		if n > 0 {
			content = append(content, buffer[:n]...)
		}

		// Check for end-of-file or other errors
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
	}

	return content, nil
}

func main() {
	// 1. Define and parse the --direct flag.
	directFlag := flag.Bool("direct", false,
		"If true, attempts to open the file with O_DIRECT for reading (platform-specific).")

	flag.Parse()

	// 2. Check for the required file name argument.
	if flag.NArg() < 1 {
		fmt.Println("Error: Missing file name argument.")
		fmt.Println("Usage: go run read.go [OPTIONS] <file-name>")
		flag.PrintDefaults()
		os.Exit(1)
	}

	fileName := flag.Arg(0)

	// 3. Determine the file open flags.
	openFlags := os.O_RDONLY
	isDirect := *directFlag

	if isDirect {
		if O_DIRECT == 0 {
			isDirect = false // Fallback to standard if O_DIRECT is not supported
			fmt.Fprintf(os.Stderr, "Warning: --direct flag used, but O_DIRECT is not defined or supported on %s. Opening with standard O_RDONLY.\n", runtime.GOOS)
		} else {
			// Combine the standard read-only flag with O_DIRECT
			openFlags |= O_DIRECT
			fmt.Fprintf(os.Stderr, "Attempting to open file '%s' with O_DIRECT (using aligned read loop)...\n", fileName)
		}
	} else {
		fmt.Fprintf(os.Stderr, "Opening file '%s' with standard flags (using io.ReadAll)...\n", fileName)
	}

	// 4. Open the file.
	file, err := os.OpenFile(fileName, openFlags, 0)
	if err != nil {
		// If O_DIRECT failed because of file system constraints (e.g., alignment),
		// the error will typically be reported here.
		fmt.Fprintf(os.Stderr, "Error opening file '%s': %v\n", fileName, err)
		os.Exit(1)
	}
	defer file.Close()

	// 5. Read the entire content of the file using the appropriate method.
	var content []byte
	if isDirect {
		// Use the aligned read loop for O_DIRECT
		content, err = readDirectAligned(file)
	} else {
		// Use standard io.ReadAll for non-direct reads
		content, err = io.ReadAll(file)
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "Error reading file content: %v\n", err)
		os.Exit(1)
	}

	// 6. Print the whole content to stdout.
	//fmt.Printf("--- File Content Start ---\n")
	fmt.Print(string(content))
	//fmt.Printf("\n--- File Content End ---\n")
}
