package poller

import (
	"context"
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
	base
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

// fetch retrieves stats from /varz and /jsz and updates the cache.
// /varz and /jsz failures gate poll-success (readiness); detail-endpoint
// failures (/jsz?detail=1, /connz) are logged but do not gate.
func (p *NATSPoller) fetch(ctx context.Context) {
	start := time.Now()
	err := p.fetchInner(ctx)
	if err != nil {
		slog.Warn("nats poller: fetch failed", "err", err)
	}
	p.recordResult(err, time.Since(start))

	// Detail endpoints are best-effort: their failures must not mark the
	// poller unready, since the primary varz/jsz cache is already populated.
	p.fetchDetail(ctx)
}

func (p *NATSPoller) fetchInner(ctx context.Context) error {
	var varz varzResponse
	if err := p.getJSON(ctx, p.url+"/varz", &varz); err != nil {
		return err
	}

	var jsz jszResponse
	if err := p.getJSON(ctx, p.url+"/jsz", &jsz); err != nil {
		return err
	}

	p.cache.SetNATSStats(store.NATSStats{
		Connections: varz.Connections,
		Streams:     jsz.NumStreams,
		InMsgs:      varz.InMsgs,
		OutMsgs:     varz.OutMsgs,
	})
	return nil
}

// fetchDetail polls /jsz?detail=1 for stream list and /connz for connections.
// Errors on any sub-request are logged and that section of the cache is left intact.
func (p *NATSPoller) fetchDetail(ctx context.Context) {
	var jszDetail jszDetailResponse
	if err := p.getJSON(ctx, p.url+"/jsz?detail=1", &jszDetail); err != nil {
		slog.Warn("nats poller: failed to fetch /jsz?detail=1", "err", err)
	} else {
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
	}

	var connz connzResponse
	if err := p.getJSON(ctx, p.url+"/connz", &connz); err != nil {
		slog.Warn("nats poller: failed to fetch /connz", "err", err)
	} else {
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
	}
}
