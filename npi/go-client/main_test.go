package main

import (
	"bytes"
	"testing"
)

func TestDefaultFlags(t *testing.T) {
	if defaultNumJobs != 112 {
		t.Fatalf("defaultNumJobs = %d, want 112", defaultNumJobs)
	}
	if *numOfWorkers != defaultNumJobs {
		t.Fatalf("*numOfWorkers = %d, want %d", *numOfWorkers, defaultNumJobs)
	}
	if *nrFiles != 10 {
		t.Fatalf("*nrFiles = %d, want 10", *nrFiles)
	}
	if *clientProtocol != "http1" {
		t.Fatalf("*clientProtocol = %q, want %q", *clientProtocol, "http1")
	}
}

func TestParseSize(t *testing.T) {
	validCases := []struct {
		input string
		want  int64
	}{
		{"0", 0},
		{"4096", 4096},
		{"128K", 128 * 1024},
		{"128k", 128 * 1024},
		{"1M", 1024 * 1024},
		{"10m", 10 * 1024 * 1024},
		{"1G", 1024 * 1024 * 1024},
		{"2g", 2 * 1024 * 1024 * 1024},
	}
	for _, tc := range validCases {
		got, err := parseSize(tc.input)
		if err != nil {
			t.Errorf("parseSize(%q) returned unexpected error: %v", tc.input, err)
			continue
		}
		if got != tc.want {
			t.Errorf("parseSize(%q) = %d, want %d", tc.input, got, tc.want)
		}
	}

	invalidCases := []string{"", "abc", "10T", "1.5M", "K", "M"}
	for _, input := range invalidCases {
		if got, err := parseSize(input); err == nil {
			t.Errorf("parseSize(%q) = %d, expected error", input, got)
		}
	}
}

func TestGetObjectPath(t *testing.T) {
	origPrefix := *objectNamePrefix
	origFileSize := *fileSizeStr
	origNrFiles := *nrFiles
	t.Cleanup(func() {
		*objectNamePrefix = origPrefix
		*fileSizeStr = origFileSize
		*nrFiles = origNrFiles
	})

	*objectNamePrefix = ""
	*fileSizeStr = "1G"
	*nrFiles = 10
	if got, want := getObjectPath(0, 1), "read/1G/10/experiment.0.1"; got != want {
		t.Errorf("getObjectPath(0, 1) = %q, want %q", got, want)
	}

	*objectNamePrefix = "go-bench/"
	*fileSizeStr = "10M"
	*nrFiles = 4
	if got, want := getObjectPath(3, 2), "go-bench/read/10M/4/experiment.3.2"; got != want {
		t.Errorf("getObjectPath(3, 2) = %q, want %q", got, want)
	}
}

func TestZeroReader(t *testing.T) {
	var r ZeroReader

	empty := []byte{}
	n, err := r.Read(empty)
	if err != nil || n != 0 {
		t.Fatalf("ZeroReader.Read(empty) = (%d, %v), want (0, nil)", n, err)
	}

	buf := bytes.Repeat([]byte{0xAB}, 64)
	n, err = r.Read(buf)
	if err != nil {
		t.Fatalf("ZeroReader.Read returned unexpected error: %v", err)
	}
	if n != len(buf) {
		t.Fatalf("ZeroReader.Read n = %d, want %d", n, len(buf))
	}
	for i, b := range buf {
		if b != 0 {
			t.Fatalf("buf[%d] = 0x%X, want 0x00", i, b)
		}
	}
}
