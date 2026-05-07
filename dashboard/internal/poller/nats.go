package poller

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/HomericIntelligence/atlas/internal/config"
	"github.com/HomericIntelligence/atlas/internal/store"
)

// varzResponse is the relevant subset of the NATS /varz monitoring endpoint.
type varzResponse struct {
	Connections int   `json:"connections"`
	InMsgs      int64 `json:"in_msgs"`
	OutMsgs     int64 `json:"out_msgs"`
}

// jszResponse is the relevant subset of the NATS /jsz monitoring endpoint.
type jszResponse struct {
	NumStreams int `json:"num_streams"`
}

// jszDetailResponse is the NATS /jsz?detail=1 response containing stream details.
type jszDetailResponse struct {
	Streams []jszStreamDetail `json:"streams"`
}

// jszStreamDetail maps to each element of the "streams" array in /jsz?detail=1.
type jszStreamDetail struct {
	Config  jszStreamConfig `json:"config"`
	State   jszStreamState  `json:"state"`
	Created time.Time       `json:"created"`
}

// jszStreamConfig holds the stream configuration sub-object.
type jszStreamConfig struct {
	Name     string   `json:"name"`
	Subjects []string `json:"subjects"`
}

// jszStreamState holds the stream state sub-object.
type jszStreamState struct {
	Messages  uint64 `json:"messages"`
	Bytes     uint64 `json:"bytes"`
	Consumers int    `json:"consumer_count"`
}

// connzResponse is the NATS /connz response.
type connzResponse struct {
	Connections []connzEntry `json:"connections"`
}

// connzEntry maps to each element of the "connections" array in /connz.
type connzEntry struct {
	Name          string `json:"name"`
	IP            string `json:"ip"`
	Subscriptions int    `json:"subscriptions"`
	InMsgs        int64  `json:"in_msgs"`
	OutMsgs       int64  `json:"out_msgs"`
	Uptime        string `json:"uptime"`
}

// NATSPoller polls the NATS monitoring endpoints for server statistics.
type NATSPoller struct {
	*base
	cache *store.Cache
	url   string
}

// NewNATSPoller constructs a NATSPoller with a 3-second HTTP timeout.
func NewNATSPoller(cfg *config.Config, cache *store.Cache) *NATSPoller {
	return &NATSPoller{
		base:  newBase("nats", &http.Client{Timeout: 3 * time.Second}),
		cache: cache,
		url:   cfg.NATSMonURL,
	}
}

// Start runs the poller in a ticker loop until ctx is cancelled.
func (p *NATSPoller) Start(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	// Fetch immediately on start.
	p.fetch(ctx)

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			p.fetch(ctx)
		}
	}
}

// fetch retrieves stats from /varz, /jsz?detail=1, and /connz and updates the
// cache. ALL three endpoints gate poll-success: any failure surfaces in
// LastError (and therefore /readyz) via errors.Join, naming which endpoint(s)
// failed. The successful endpoints still update their cache slices — partial
// freshness is fine; what's NOT fine is misreporting "everything fresh" to
// /readyz while one of the caches is silently stale.
//
// Per-endpoint failures also increment atlas_poll_endpoint_errors_total via
// MetricsSink.IncEndpointError so operators can attribute failures to a
// specific endpoint without inflating the cycle-level atlas_poll_errors_total
// counter (which still increments exactly once per failed cycle, regardless
// of how many of the three endpoints failed).
func (p *NATSPoller) fetch(ctx context.Context) {
	start := time.Now()
	errs := []error{
		p.fetchVarzJsz(ctx),
		p.fetchStreams(ctx),
		p.fetchConns(ctx),
	}
	joined := errors.Join(errs...)
	if joined != nil {
		slog.Warn("nats poller: one or more endpoints failed", "err", joined)
	}
	p.recordResult(joined, time.Since(start))
}

// fetchVarzJsz polls /varz and /jsz and writes the aggregate NATSStats slice.
// /jsz here is the lightweight summary endpoint; the detail endpoint is polled
// separately by fetchStreams.
func (p *NATSPoller) fetchVarzJsz(ctx context.Context) error {
	var varz varzResponse
	if err := p.getJSON(ctx, p.url+"/varz", &varz); err != nil {
		p.incEndpointError("varz")
		return fmt.Errorf("varz: %w", err)
	}

	var jsz jszResponse
	if err := p.getJSON(ctx, p.url+"/jsz", &jsz); err != nil {
		p.incEndpointError("jsz")
		return fmt.Errorf("jsz: %w", err)
	}

	p.cache.SetNATSStats(store.NATSStats{
		Connections: varz.Connections,
		Streams:     jsz.NumStreams,
		InMsgs:      varz.InMsgs,
		OutMsgs:     varz.OutMsgs,
	})
	return nil
}

// fetchStreams polls /jsz?detail=1 for the stream list. On error the
// stream-list section of the cache is left intact (the previous values stay
// in place rather than being cleared) but the failure is reported to the
// caller so /readyz reflects the staleness.
func (p *NATSPoller) fetchStreams(ctx context.Context) error {
	var jszDetail jszDetailResponse
	if err := p.getJSON(ctx, p.url+"/jsz?detail=1", &jszDetail); err != nil {
		p.incEndpointError("jsz_detail")
		return fmt.Errorf("jsz_detail: %w", err)
	}
	streams := make([]store.NATSStreamInfo, 0, len(jszDetail.Streams))
	for _, s := range jszDetail.Streams {
		streams = append(streams, store.NATSStreamInfo{
			Name:      s.Config.Name,
			Subjects:  s.Config.Subjects,
			Messages:  s.State.Messages,
			Bytes:     s.State.Bytes,
			Consumers: s.State.Consumers,
			Created:   s.Created,
		})
	}
	p.cache.SetNATSStreams(streams)
	return nil
}

// fetchConns polls /connz for the active client connection list. On error the
// connection-list section of the cache is left intact and the failure is
// reported to the caller so /readyz reflects the staleness — closes the §3
// audit MAJOR finding "NATSPoller readiness asymmetry."
func (p *NATSPoller) fetchConns(ctx context.Context) error {
	var connz connzResponse
	if err := p.getJSON(ctx, p.url+"/connz", &connz); err != nil {
		p.incEndpointError("connz")
		return fmt.Errorf("connz: %w", err)
	}
	conns := make([]store.NATSConnInfo, 0, len(connz.Connections))
	for _, c := range connz.Connections {
		conns = append(conns, store.NATSConnInfo{
			Name:          c.Name,
			IP:            c.IP,
			Subscriptions: c.Subscriptions,
			InMsgs:        c.InMsgs,
			OutMsgs:       c.OutMsgs,
			Uptime:        c.Uptime,
		})
	}
	p.cache.SetNATSConns(conns)
	return nil
}
