package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"strings"
	"time"

	"cloud.google.com/go/logging"
	"cloud.google.com/go/logging/logadmin"
	"google.golang.org/api/iterator"
	"google.golang.org/genai"
)

const (
	contextLookback    = 2 * time.Minute
	contextLookforward = 1 * time.Minute

	geminiPromptTemplate = `
	You are a Google Cloud Support Engineer expert in GKE and GCSFuse.
	Analyze the following log sequence from the gke-gcsfuse-sidecar.
	The logs are provided in chronological order.
	
	Focus on:
	1. Ignore "failed to calculate volume total size for" kind of error
	2. What triggered the first error real error which cause failure in model running? (Look at the INFO logs immediately preceding the ERROR). Please be straightforward and don't wrote extra info.
	3. Is this a permission issue (403), network (timeout), or configuration?
	4. Does the model get crashed or failed? If yes what gcsfuse error cause model to get crashed?

	LOGS:
	%s
	`
	geminiModel    = "gemini-2.5-flash"
	maxContextLogs = 500
)

// Config holds our runtime flags
type Config struct {
	ProjectID string
	Region    string
	PodName   string

	// Time Flags
	Lookback    time.Duration
	StartString string // New flag for explicit start
	EndString   string // New flag for explicit end
}

func main() {
	// 1. Parse Flags
	cfg := parseConfig()

	// 2. Resolve Time Window
	searchStart, searchEnd, err := resolveTimeWindow(cfg)
	if err != nil {
		log.Fatalf("Time window error: %v", err)
	}

	ctx := context.Background()
	logClient, err := logadmin.NewClient(ctx, cfg.ProjectID)
	if err != nil {
		log.Fatalf("Failed to create logging client: %v", err)
	}
	defer logClient.Close()

	// 3. Step 1: Find the "Anchor" (The Error within the window)
	anchorEntry, err := findAnchorError(ctx, logClient, cfg, searchStart, searchEnd)
	if err != nil {
		log.Fatalf("Error reading logs: %v", err)
	}
	if anchorEntry == nil {
		fmt.Println("✅ No GCSFuse errors found in the specified window.")
		return
	}

	fmt.Printf("🚨 Found Error at %s: %v\n", anchorEntry.Timestamp.Format(time.TimeOnly), parsePayload(anchorEntry.Payload))

	// 4. Step 2: Expand Context (2 mins before the found error)
	logDump, err := fetchLogContext(ctx, logClient, anchorEntry, cfg)
	if err != nil {
		log.Fatalf("Error fetching context logs: %v", err)
	}

	// 5. Step 3: Send to Gemini
	fmt.Println("🧠 Sending to Gemini for analysis...")
	analysis, err := analyzeWithGemini(ctx, cfg.ProjectID, cfg.Region, logDump)
	if err != nil {
		log.Fatalf("Gemini analysis failed: %v", err)
	}

	// 6. Output Result
	printReport(analysis)
}

// resolveTimeWindow handles the logic between explicit (-start) vs relative (-lookback) time
func resolveTimeWindow(cfg Config) (time.Time, time.Time, error) {
	// Case 1: Relative Mode (Default)
	if cfg.StartString == "" {
		end := time.Now()
		start := end.Add(-cfg.Lookback)
		return start, end, nil
	}

	// Case 2: Explicit Mode
	start, err := time.Parse(time.RFC3339, cfg.StartString)
	if err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("invalid start time format (use RFC3339 e.g., 2025-01-02T15:04:05Z): %v", err)
	}

	end := time.Now()
	if cfg.EndString != "" {
		end, err = time.Parse(time.RFC3339, cfg.EndString)
		if err != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid end time format: %v", err)
		}
	}

	if end.Before(start) {
		return time.Time{}, time.Time{}, fmt.Errorf("end time cannot be before start time")
	}

	return start, end, nil
}

func parseConfig() Config {
	cfg := Config{}
	flag.StringVar(&cfg.ProjectID, "project", "", "GCP Project ID")
	flag.StringVar(&cfg.Region, "region", "us-central1", "Vertex AI Region")
	flag.StringVar(&cfg.PodName, "pod", "", "Specific Pod Name (optional)")

	// Time Window Flags
	flag.DurationVar(&cfg.Lookback, "lookback", 1*time.Hour, "Relative lookback window (e.g., 1h, 30m). Ignored if -start is set.")
	flag.StringVar(&cfg.StartString, "start", "", "Explicit Start Time (RFC3339 format, e.g., 2025-01-07T10:00:00Z)")
	flag.StringVar(&cfg.EndString, "end", "", "Explicit End Time (RFC3339). Defaults to Now if not set.")

	flag.Parse()

	if cfg.ProjectID == "" {
		log.Fatal("Please provide -project <PROJECT_ID>")
	}
	return cfg
}

func getBaseFilter(podName string) string {
	baseFilter := `resource.type="k8s_container" AND resource.labels.container_name="gke-gcsfuse-sidecar"`
	if podName != "" {
		baseFilter += fmt.Sprintf(` AND resource.labels.pod_name="%s"`, podName)
	}
	return baseFilter
}

func findAnchorError(ctx context.Context, client *logadmin.Client, cfg Config, start, end time.Time) (*logging.Entry, error) {
	fmt.Printf("🔍 Scanning logs for GCSFuse errors between %s and %s...\n",
		start.Format(time.TimeOnly), end.Format(time.TimeOnly))

	baseFilter := getBaseFilter(cfg.PodName)

	// Strict filter: Error must be INSIDE the requested window
	anchorFilter := fmt.Sprintf(`%s AND severity>=ERROR AND timestamp >= "%s" AND timestamp <= "%s"`,
		baseFilter, start.Format(time.RFC3339), end.Format(time.RFC3339))

	// Fetch the most recent error inside that window
	iter := client.Entries(ctx, logadmin.Filter(anchorFilter))
	anchorEntry, err := iter.Next()

	if err == iterator.Done {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return anchorEntry, nil
}

func fetchLogContext(ctx context.Context, client *logadmin.Client, anchorEntry *logging.Entry, cfg Config) (string, error) {
	// Note: We respect the error time, not the window boundaries, for context.
	// If the error was at 10:00:05, we want logs from 09:58:05, even if the user said -start 10:00.
	errorTime := anchorEntry.Timestamp
	contextStart := errorTime.Add(-contextLookback).Format(time.RFC3339)
	contextEnd := errorTime.Add(contextLookforward).Format(time.RFC3339)

	fmt.Println("📜 Fetching surrounding logs (context window)...")

	baseFilter := getBaseFilter(cfg.PodName)
	contextFilter := fmt.Sprintf(`%s AND timestamp >= "%s" AND timestamp <= "%s"`, baseFilter, contextStart, contextEnd)
	cIter := client.Entries(ctx, logadmin.Filter(contextFilter))

	var tempLogs []string
	count := 0
	for {
		e, err := cIter.Next()
		if err == iterator.Done {
			break
		}
		if err != nil {
			log.Printf("Warning: error fetching log line: %v", err)
			continue
		}
		if count >= maxContextLogs {
			break
		}

		line := fmt.Sprintf("[%s] [%s] %v", e.Timestamp.Format("15:04:05"), e.Severity, parsePayload(e.Payload))
		tempLogs = append(tempLogs, line)
		count++
	}

	// Reverse logs to be Chronological
	for i, j := 0, len(tempLogs)-1; i < j; i, j = i+1, j-1 {
		tempLogs[i], tempLogs[j] = tempLogs[j], tempLogs[i]
	}

	return strings.Join(tempLogs, "\n"), nil
}

func printReport(analysis string) {
	fmt.Println("\n" + strings.Repeat("-", 50))
	fmt.Println("🕵️  LOG DETECTIVE REPORT")
	fmt.Println(strings.Repeat("-", 50))
	fmt.Println(analysis)
}

// ... [analyzeWithGemini and parsePayload functions remain exactly the same] ...
func analyzeWithGemini(ctx context.Context, projectID, region, logs string) (string, error) {
	client, err := genai.NewClient(ctx, &genai.ClientConfig{
		Project:  projectID,
		Location: region,
		Backend:  genai.BackendVertexAI,
	})
	if err != nil {
		return "", fmt.Errorf("failed to create genai client: %w", err)
	}

	prompt := fmt.Sprintf(geminiPromptTemplate, logs)

	resp, err := client.Models.GenerateContent(ctx, geminiModel, genai.Text(prompt), nil)
	if err != nil {
		return "", fmt.Errorf("failed to generate content: %w", err)
	}
	return resp.Text(), nil
}

func parsePayload(p interface{}) string {
	switch v := p.(type) {
	case string:
		return v
	case []byte:
		return string(v)
	default:
		return fmt.Sprintf("%v", v)
	}
}
