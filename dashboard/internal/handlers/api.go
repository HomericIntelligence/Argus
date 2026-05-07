// Package handlers contains the HTTP request handlers exposed by the Atlas
// dashboard: REST/JSON API endpoints, the SSE event stream, and the host
// detail pages.
package handlers

import (
	"encoding/json"
	"net/http"

	"github.com/HomericIntelligence/atlas/internal/store"
)

// Hosts is the handler for GET /api/hosts.
// It returns a JSON array of HostView, one entry per Tailscale device, with
// real TailscaleIP and Online fields from the device cache, and service probe
// results from the probe cache.
type Hosts struct {
	cache *store.Cache
}

// NewHosts constructs a Hosts handler backed by the given cache.
func NewHosts(cache *store.Cache) *Hosts {
	return &Hosts{cache: cache}
}

// ServeHTTP implements http.Handler.
func (h *Hosts) ServeHTTP(w http.ResponseWriter, _ *http.Request) {
	views := store.BuildHostViews(h.cache)

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(views); err != nil {
		http.Error(w, "failed to encode response", http.StatusInternalServerError)
	}
}
