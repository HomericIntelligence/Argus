package catalog

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"sync"
	"time"
)

// HostAddr represents a node to probe.
type HostAddr struct {
	Hostname string
	IP       string
}

// ProbeResult captures the outcome of a single health-check probe.
type ProbeResult struct {
	ServiceDef
	Host      string
	URL       string
	OK        bool
	LatencyMs int64
	CheckedAt time.Time
}

// probeWork is a single unit of work for a probe worker.
type probeWork struct {
	host HostAddr
	svc  ServiceDef
}

// ProbeAll probes every host × every service concurrently.
// Worker pool: 16 goroutines. Per-probe HTTP timeout: 1s.
// Uses GET against http://{host.IP}:{svc.Port}{svc.HealthPath}.
// client parameter allows injection in tests (pass nil for default).
func ProbeAll(ctx context.Context, hosts []HostAddr, client *http.Client) []ProbeResult {
	if client == nil {
		client = &http.Client{Timeout: 1 * time.Second}
	}

	totalWork := len(hosts) * len(KnownServices)
	if totalWork == 0 {
		return nil
	}

	workCh := make(chan probeWork, totalWork)
	for _, h := range hosts {
		for _, svc := range KnownServices {
			workCh <- probeWork{host: h, svc: svc}
		}
	}
	close(workCh)

	resultCh := make(chan ProbeResult, totalWork)

	const workerCount = 16

	var wg sync.WaitGroup
	for i := 0; i < workerCount; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for w := range workCh {
				resultCh <- doProbe(ctx, client, w.host, w.svc)
			}
		}()
	}

	// Close resultCh once all workers finish.
	go func() {
		wg.Wait()
		close(resultCh)
	}()

	results := make([]ProbeResult, 0, totalWork)
	for r := range resultCh {
		results = append(results, r)
	}

	// Sort by host then service name for deterministic output.
	sort.Slice(results, func(i, j int) bool {
		if results[i].Host != results[j].Host {
			return results[i].Host < results[j].Host
		}
		return results[i].Name < results[j].Name
	})

	return results
}

// doProbe performs a single HTTP GET and returns a ProbeResult.
func doProbe(ctx context.Context, client *http.Client, host HostAddr, svc ServiceDef) ProbeResult {
	url := fmt.Sprintf("http://%s:%d%s", host.IP, svc.Port, svc.HealthPath)
	start := time.Now()

	var ok bool
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err == nil {
		resp, doErr := client.Do(req)
		if doErr == nil {
			ok = resp.StatusCode < 400
			_ = resp.Body.Close()
		}
	}

	latency := time.Since(start).Milliseconds()
	return ProbeResult{
		ServiceDef: svc,
		Host:       host.Hostname,
		URL:        url,
		OK:         ok,
		LatencyMs:  latency,
		CheckedAt:  start,
	}
}
