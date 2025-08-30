package main

/*
#cgo CFLAGS: -I/usr/include/fuse3
#cgo LDFLAGS: -lfuse3 -lpthread
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "fuse_bridge.h"
*/
import "C"

import (
	"context"
	"fmt"
	"os"
	"strings"
	"syscall"
	"unsafe"

	"cloud.google.com/go/storage"
	"google.golang.org/api/iterator"
)

var gcsClient *storage.Client
var bucketName string

//export gcs_getattr
func gcs_getattr(path *C.char, stbuf *C.struct_stat, fi *C.struct_fuse_file_info) C.int {
	goPath := C.GoString(path)
	fmt.Printf("getattr called for path: %s\n", goPath)

	C.memset(unsafe.Pointer(stbuf), 0, C.sizeof_struct_stat)

	if goPath == "/" {
		stbuf.st_mode = syscall.S_IFDIR | 0755
		stbuf.st_nlink = 2
		return 0
	}

	// For now, we only support a flat directory structure.
	// We'll check if the path corresponds to an object in the bucket.
	objectName := strings.TrimPrefix(goPath, "/")
	obj := gcsClient.Bucket(bucketName).Object(objectName)
	attrs, err := obj.Attrs(context.Background())
	if err != nil {
		if err == storage.ErrObjectNotExist {
			return -C.ENOENT
		}
		fmt.Printf("Error getting object attributes: %v\n", err)
		return -C.EIO
	}

	stbuf.st_mode = syscall.S_IFREG | 0444
	stbuf.st_nlink = 1
	stbuf.st_size = C.long(attrs.Size)
	return 0
}

//export gcs_readdir
func gcs_readdir(path *C.char, buf unsafe.Pointer, filler C.fuse_fill_dir_t, offset C.off_t, fi *C.struct_fuse_file_info, flags C.enum_fuse_readdir_flags) C.int {
	goPath := C.GoString(path)
	fmt.Printf("readdir called for path: %s\n", goPath)

	if goPath != "/" {
		return -C.ENOENT
	}

	C.helper_fill_dir(filler, buf, C.CString("."), nil, 0)
	C.helper_fill_dir(filler, buf, C.CString(".."), nil, 0)

	it := gcsClient.Bucket(bucketName).Objects(context.Background(), nil)
	for {
		attrs, err := it.Next()
		if err == iterator.Done {
			break
		}
		if err != nil {
			fmt.Printf("Error iterating objects: %v\n", err)
			return -C.EIO
		}
		C.helper_fill_dir(filler, buf, C.CString(attrs.Name), nil, 0)
	}

	return 0
}

//export gcs_open
func gcs_open(path *C.char, fi *C.struct_fuse_file_info) C.int {
	goPath := C.GoString(path)
	fmt.Printf("open called for path: %s\n", goPath)

	objectName := strings.TrimPrefix(goPath, "/")
	obj := gcsClient.Bucket(bucketName).Object(objectName)
	if _, err := obj.Attrs(context.Background()); err != nil {
		if err == storage.ErrObjectNotExist {
			return -C.ENOENT
		}
		return -C.EIO
	}

	return 0
}

//export gcs_read
func gcs_read(path *C.char, buf *C.char, size C.size_t, offset C.off_t, fi *C.struct_fuse_file_info) C.int {
	goPath := C.GoString(path)
	fmt.Printf("read called for path: %s, size: %d, offset: %d\n", goPath, size, offset)

	objectName := strings.TrimPrefix(goPath, "/")
	obj := gcsClient.Bucket(bucketName).Object(objectName)

	r, err := obj.NewRangeReader(context.Background(), int64(offset), int64(size))
	if err != nil {
		fmt.Printf("Error creating range reader: %v\n", err)
		return -C.EIO
	}
	defer r.Close()

	data := make([]byte, size)
	n, err := r.Read(data)
	if err != nil && err.Error() != "EOF" {
		fmt.Printf("Error reading data: %v\n", err)
		return -C.EIO
	}

	C.memcpy(unsafe.Pointer(buf), unsafe.Pointer(&data[0]), C.size_t(n))

	return C.int(n)
}

func main() {
	fmt.Println("gcsfuse-go starting up")
	if len(os.Args) < 3 {
		fmt.Fprintf(os.Stderr, "Usage: %s <bucket-name> <mountpoint>\n", os.Args[0])
		os.Exit(1)
	}

	bucketName = os.Args[1]
	mountpoint := os.Args[2]
	fmt.Printf("Mounting bucket %s at %s\n", bucketName, mountpoint)

	var err error
	gcsClient, err = storage.NewClient(context.Background())
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create GCS client: %v\n", err)
		os.Exit(1)
	}

	// We need to pass only the mountpoint argument to fuse_main.
	fuseArgs := []string{os.Args[0], mountpoint}
	argc := C.int(len(fuseArgs))
	argv := make([]*C.char, len(fuseArgs))
	for i, s := range fuseArgs {
		argv[i] = C.CString(s)
		defer C.free(unsafe.Pointer(argv[i]))
	}

	fmt.Printf("Starting fuse\n")
	C.start_fuse(argc, &argv[0])
	fmt.Println("gcsfuse-go finished")
}
