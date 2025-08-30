#ifndef FUSE_BRIDGE_H
#define FUSE_BRIDGE_H

#define FUSE_USE_VERSION 31
#include <fuse3/fuse.h>

int start_fuse(int argc, char *argv[]);
int helper_fill_dir(fuse_fill_dir_t filler, void *buf, const char *name, const struct stat *stbuf, off_t off);
int c_gcs_open(const char *path, struct fuse_file_info *fi);
int c_gcs_read(const char *path, char *buf, size_t size, off_t offset, struct fuse_file_info *fi);

#endif // FUSE_BRIDGE_H
