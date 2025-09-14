package main

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"cloud.google.com/go/storage"
	"golang.org/x/sync/errgroup"
	"google.golang.org/api/iterator"
)

var (
	fileSize    = flag.String("filesize", "1M", "Size of the file to create (e.g., 1K, 1M, 1G)")
	numJobs     = flag.Int("numjobs", 1, "Number of upload jobs per file group")
	nrFile      = flag.Int("nrfile", 1, "Number of file groups")
	parallelism = flag.Int("parallelism", 80, "Number of parallel uploads")
	region      = flag.String("region", "", "Region")
	projectID   = flag.String("project", "", "Google Cloud project ID (required)")
	bucket      = flag.String("bucket", "", "bucket name")
	opType      = flag.String("op_type", "", "setup or delete")
	benchType   = flag.String("bench_type", "", "rand-read or seq-read")
)

// parallelCopyObjects copies a list of objects from a source bucket to a destination bucket in parallel.
func parallelCopyObjects(projectID, copyBucket string, numJobs, nrfile, parallelization int) error {
	ctx := context.Background()

	// 1. Create a single, shared GCS client. It's safe for concurrent use.
	client, err := storage.NewClient(ctx)
	if err != nil {
		return fmt.Errorf("storage.NewClient: %w", err)
	}
	defer client.Close()

	totalJobs := numJobs * nrfile
	if totalJobs == 0 {
		log.Printf("No objects to copy.")
		return nil
	}

	// 2. Set up channels and a WaitGroup.
	jobs := make(chan string, totalJobs)
	errs := make(chan error, totalJobs)
	var wg sync.WaitGroup

	// 3. Start the worker pool.
	// We subtract 1 because the source object (0,0) is not copied.
	log.Printf("🚀 Starting %d workers to copy %d objects...", parallelization, totalJobs-1)
	for i := 0; i < parallelization; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			// Each worker pulls an object name from the jobs channel until it's closed.
			for objectName := range jobs {
				// The first object (0,0) is the source and already exists, so we skip copying it.
				if objectName == *benchType+".0.0" {
					continue
				}

				src := client.Bucket(*bucket).Object(*benchType + ".0.0")
				dst := client.Bucket(*bucket).Object(objectName)

				var lastErr error
				for attempt := 0; attempt < 5; attempt++ {
					copier := dst.CopierFrom(src)
					copyCtx, cancel := context.WithTimeout(ctx, time.Second*120)

					if _, err := copier.Run(copyCtx); err != nil {
						lastErr = err
						cancel() // Always cancel on error
					} else {
						lastErr = nil
						cancel() // Always cancel on success
						break    // Success, exit retry loop
					}
				}

				if lastErr != nil {
					errs <- fmt.Errorf("worker %d failed to copy to %s after 5 attempts: %w", workerID, objectName, lastErr)
				}
			}
		}(i + 1)
	}

	// 4. Produce jobs.
	for j := 0; j < numJobs; j++ {
		for n := 0; n < nrfile; n++ {
			jobs <- *benchType + "." + strconv.Itoa(j) + "." + strconv.Itoa(n)
		}
	}
	close(jobs) // Close the channel to signal workers that no more jobs are coming.

	// 5. Wait and collect results.
	wg.Wait()   // Wait for all workers to finish.
	close(errs) // Close the errors channel.

	// Check if any errors occurred.
	var allErrors []string
	for err := range errs {
		allErrors = append(allErrors, err.Error())
	}

	if len(allErrors) > 0 {
		return fmt.Errorf("finished with %d errors:\n- %s", len(allErrors), strings.Join(allErrors, "\n- "))
	}
	log.Printf("\n🎉 All objects copied successfully!\n")
	return nil
}

func deleteObjectsParallel(ctx context.Context, client *storage.Client, projectID, bucketName string, parallelism int) error {
	// Ensure at least one worker to prevent deadlock if parallelism is 0 or negative.
	if parallelism < 1 {
		parallelism = 1
	}

	// Constants for retry logic
	const maxAttempts = 4                  // 1 initial attempt + 3 retries
	const initialBackoff = 1 * time.Second // Starting backoff duration

	// Use a buffered channel for the job queue.
	jobs := make(chan string, 100)

	// Use an errgroup to manage worker goroutines and capture the first error.
	// gctx will be canceled if any goroutine in the group returns an error.
	g, gctx := errgroup.WithContext(ctx)
	bkt := client.Bucket(bucketName)

	// Start the worker pool (consumers)
	for i := 0; i < parallelism; i++ {
		g.Go(func() error {
			// Each worker ranges over the jobs channel until it's closed.
			for objectName := range jobs {
				var err error
				backoff := initialBackoff

				// --- Retry loop for Delete ---
				for attempt := 0; attempt < maxAttempts; attempt++ {
					// Before each attempt, check if the group context is canceled
					select {
					case <-gctx.Done():
						return gctx.Err() // Stop retrying if context is canceled
					default:
						// Continue
					}

					obj := bkt.Object(objectName)
					err = obj.Delete(gctx)
					if err == nil {
						break // Success, exit retry loop
					}

					// Log the error (optional, but helpful for debugging)
					// log.Printf("Attempt %d/%d: failed to delete %s: %v", attempt+1, maxAttempts, objectName, err)

					// If this was the last attempt, break out to return the error
					if attempt == maxAttempts-1 {
						break
					}

					// Do not retry context cancellation errors; they are terminal.
					if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
						return err
					}

					// Wait before the next retry, respecting context cancellation
					select {
					case <-time.After(backoff):
						backoff *= 2 // Exponential backoff
					case <-gctx.Done():
						return gctx.Err() // Canceled while waiting to retry
					}
				}

				// If err is still not nil after all attempts, return it to the errgroup
				if err != nil {
					return fmt.Errorf("failed to delete object %s after %d attempts: %w", objectName, maxAttempts, err)
				}
				// Otherwise, loop to the next objectName
			}
			return nil
		})
	}

	// Start the lister (producer) in its own goroutine
	g.Go(func() error {
		defer close(jobs) // ALWAYS close jobs channel when producer exits

		it := bkt.Objects(gctx, nil)
		for {
			var attrs *storage.ObjectAttrs
			var err error
			backoff := initialBackoff

			// --- Retry loop for List (it.Next) ---
			for attempt := 0; attempt < maxAttempts; attempt++ {
				select {
				case <-gctx.Done():
					return gctx.Err()
				default:
				}

				attrs, err = it.Next()
				if err == nil {
					break // Success
				}

				// iterator.Done is not an error to retry; it's the signal we're finished.
				if err == iterator.Done {
					break // Exit retry loop; outer loop will handle 'Done'
				}

				// Do not retry context cancellation errors
				if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
					return err
				}

				// log.Printf("Attempt %d/%d: failed to list next object: %v", attempt+1, maxAttempts, err)

				if attempt == maxAttempts-1 {
					break // Last attempt failed, break to return error
				}

				// Wait with backoff
				select {
				case <-time.After(backoff):
					backoff *= 2
				case <-gctx.Done():
					return gctx.Err()
				}
			}

			// --- Handle result of the retry loop ---
			if err == iterator.Done {
				break // Finished listing all objects, exit the producer's main loop
			}
			if err != nil {
				// All attempts to list the next object failed.
				return fmt.Errorf("failed to list objects after %d attempts: %w", maxAttempts, err)
			}

			// If we are here, err is nil and attrs is valid. Send job to workers.
			select {
			case jobs <- attrs.Name:
			case <-gctx.Done():
				return gctx.Err()
			}
		}
		return nil
	})

	// Wait for all goroutines (workers and lister) to finish.
	// Returns the first non-nil error from any of them.
	return g.Wait()
}

// createBucket creates a GCS bucket if it does not already exist.
func createBucket(ctx context.Context, client *storage.Client, projectID, bucketName, region string) error {
	log.Printf("Attempting to create bucket gs://%s in region %s", bucketName, region)
	bucket := client.Bucket(bucketName)
	attrs := &storage.BucketAttrs{
		Location: region,
	}
	if err := bucket.Create(ctx, projectID, attrs); err != nil {
		return fmt.Errorf("failed to create bucket %q: %w", bucketName, err)
	}
	log.Printf("Bucket gs://%s created in region %s.", bucketName, region)
	return nil
}

// deleteBucket deletes a GCS bucket if it exists.
func deleteBucket(ctx context.Context, client *storage.Client, bucketName string) error {
	log.Printf("Attempting to delete bucket gs://%s...", bucketName)
	bucket := client.Bucket(bucketName)
	if err := bucket.Delete(ctx); err != nil {
		return fmt.Errorf("failed to delete bucket %q: %w", bucketName, err)
	}
	return nil
}

// parseSize converts a string like "10M" or "1G" into a number of bytes.
func parseSize(sizeStr string) (int64, error) {
	re := regexp.MustCompile(`^(\d+)([KMG])`)
	matches := re.FindStringSubmatch(strings.ToUpper(sizeStr))
	if len(matches) != 3 {
		return 0, fmt.Errorf("invalid size format: %q. Use format like 1K, 2M, 3G", sizeStr)
	}

	size, err := strconv.ParseInt(matches[1], 10, 64)
	if err != nil {
		return 0, err
	}

	unit := matches[2]
	switch unit {
	case "K":
		return size * 1024, nil
	case "M":
		return size * 1024 * 1024, nil
	case "G":
		return size * 1024 * 1024 * 1024, nil
	default:
		return 0, fmt.Errorf("unknown unit: %s", unit)
	}
}

// createObject creates a GCS object of a given name and size by writing a buffer repeatedly.
/*
 * createObject creates an object of a specific size with zero data.
 *
 * It allocates a 32MB in-memory buffer and writes it repeatedly to the GCS
 * writer until the target size is reached. This is a memory-efficient way
 * to generate large files for benchmarking.
 */
func createObject(ctx context.Context, client *storage.Client, bucketName, objectName string, size int64) (err error) {
	log.Printf("Attempting to create object gs://%s/%s of size %d bytes...", bucketName, objectName, size)
	wc := client.Bucket(bucketName).Object(objectName).NewWriter(ctx)

	wc.ChunkSize = int(size)

	defer func() {
		if closeErr := wc.Close(); closeErr != nil {
			if err == nil {
				err = fmt.Errorf("failed to close GCS writer: %w", closeErr)
			}
		} else if err == nil {
			log.Printf("Successfully created object gs://%s/%s", bucketName, objectName)
		}
	}()

	// Create a 32MB buffer. The slice is zero-filled by default.
	const bufferSize = 32 * 1024 * 1024
	buffer := make([]byte, bufferSize)

	var written int64
	for written < size {
		bytesToWrite := int64(len(buffer))
		if size-written < bytesToWrite {
			bytesToWrite = size - written
		}

		n, writeErr := io.Copy(wc, bytes.NewReader(buffer[:bytesToWrite]))
		if writeErr != nil {
			err = fmt.Errorf("failed during Write to GCS after %d bytes: %w", written, writeErr)
			return err
		}
		written += int64(n)
		log.Printf("Written %d", written)
	}

	return nil
}

func main() {
	startTime := time.Now()
	defer func() {
		fmt.Printf("Total time elapsed in secs: %v", time.Since(startTime).Seconds())
	}()
	flag.Parse()
	if *projectID == "" || *bucket == "" || *opType == "" || *region == "" || *benchType == "" {
		log.Fatal("Error: --project, --bucket, --op_type, --bench_type, and --region are required.")
	}
	if *fileSize == "" || *numJobs <= 0 || *nrFile <= 0 || *parallelism <= 0 {
		log.Fatal("Error: filesize, num_jobs, num_files, and parallelism must be positive.")
	}
	ctx := context.Background()
	client, err := storage.NewClient(ctx)
	if err != nil {
		log.Fatalf("Failed to create storage client: %v", err)
	}
	defer client.Close()

	if *opType == "setup" {
		if err := createBucket(ctx, client, *projectID, *bucket, *region); err != nil {
			log.Fatalf("Failed to create bucket: %v", err)
		}
		if *benchType == "rand-read" || *benchType == "seq-read" {
			size, err := parseSize(*fileSize)
			if err != nil {
				log.Fatalf("Failed to parse file size: %v", err)
			}
			err = createObject(ctx, client, *bucket, *benchType+".0.0", size)
			if err != nil {
				log.Fatalf("Failed to create object: %v", err)
			}
			err = parallelCopyObjects(*projectID, *bucket, *numJobs, *nrFile, *parallelism)
			if err != nil {
				log.Fatalf("Failed to copy objects: %v", err)
			}
		}

	} else if *opType == "delete" {
		if err := deleteObjectsParallel(ctx, client, *projectID, *bucket, *parallelism); err != nil {
			log.Fatalf("Failed to delete objects: %v", err)
		}
		if err := deleteBucket(ctx, client, *bucket); err != nil {
			log.Fatalf("Failed to delete bucket: %v", err)
		}
		log.Printf("Bucket gs://%s deleted successfully.", *bucket)
	} else {
		log.Fatal("Error: --op_type must be either 'setup' or 'delete'.")
	}
}
