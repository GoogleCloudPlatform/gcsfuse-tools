#include "fuse_bridge.h"
#include "_cgo_export.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

int helper_fill_dir(fuse_fill_dir_t filler, void *buf, const char *name, const struct stat *stbuf, off_t off) {
    return filler(buf, name, stbuf, off, 0);
}

static int c_gcs_getattr(const char *path, struct stat *stbuf, struct fuse_file_info *fi) {
    return gcs_getattr((char*)path, stbuf, fi);
}

static int c_gcs_readdir(const char *path, void *buf, fuse_fill_dir_t filler, off_t offset, struct fuse_file_info *fi, enum fuse_readdir_flags flags) {
    return gcs_readdir((char*)path, buf, filler, offset, fi, flags);
}

int c_gcs_open(const char *path, struct fuse_file_info *fi) {
    return gcs_open((char*)path, fi);
}

int c_gcs_read(const char *path, char *buf, size_t size, off_t offset, struct fuse_file_info *fi) {
    return gcs_read((char*)path, buf, size, offset, fi);
}

static struct fuse_operations gcs_oper = {
    .getattr = c_gcs_getattr,
    .readdir = c_gcs_readdir,
    .open    = c_gcs_open,
    .read    = c_gcs_read,
};

// This function will be called from Go to start the FUSE main loop.
int start_fuse(int argc, char *argv[]) {
    return fuse_main(argc, argv, &gcs_oper, NULL);
}
