package handlers

import (
	"log/slog"
	"net/http"
	"regexp"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/HomericIntelligence/atlas/internal/grafana"
	"github.com/HomericIntelligence/atlas/internal/mnemosyne"
	"github.com/HomericIntelligence/atlas/internal/store"
	"github.com/HomericIntelligence/atlas/web/templates"
)

// validTimeRange matches Grafana relative time expressions (now, now-1h, now-7d, etc.)
// and absolute epoch-millisecond timestamps (13-digit numbers).
var validTimeRange = regexp.MustCompile(`^(now(-[0-9]+(s|m|h|d|w|y))?|[0-9]{13})$`)

// HostsHandler serves the /hosts page and the /partials/host/{name} fragment.
type HostsHandler struct {
	cache        *store.Cache
	grafanaURL   string
	natsDashURL  string
	natsTopURL   string
	natsMon      string
	mnemoReader  *mnemosyne.Reader
}

// NewHostsHandler creates a HostsHandler backed by the given cache.
func NewHostsHandler(cache *store.Cache) *HostsHandler {
	return &HostsHandler{cache: cache}
}

// WithMnemoReader sets the Mnemosyne skills reader on the handler.
func (h *HostsHandler) WithMnemoReader(r *mnemosyne.Reader) *HostsHandler {
	h.mnemoReader = r
	return h
}

// WithGrafanaURL returns a copy of the HostsHandler with the Grafana base URL set.
func (h *HostsHandler) WithGrafanaURL(url string) *HostsHandler {
	h.grafanaURL = url
	return h
}

// WithNATSURLs sets the NATS external link URLs on the handler.
func (h *HostsHandler) WithNATSURLs(dashURL, topURL, monURL string) *HostsHandler {
	h.natsDashURL = dashURL
	h.natsTopURL = topURL
	h.natsMon = monURL
	return h
}

// ServeHTTP renders the full hosts page.
func (h *HostsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	views := store.BuildHostViews(h.cache)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.HostsPage(views), "/hosts")
}

// Partial renders a single host card fragment for HTMX polling.
func (h *HostsHandler) Partial(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	views := store.BuildHostViews(h.cache)
	for _, v := range views {
		if v.Hostname == name {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			renderTempl(w, r, templates.HostCard(v), "/partials/host/{name}")
			return
		}
	}
	http.NotFound(w, r)
}

// AgentsPage renders the full agents list page with optional filters.
func (h *HostsHandler) AgentsPage(w http.ResponseWriter, r *http.Request) {
	agents := h.cache.GetAgents()
	search := r.URL.Query().Get("search")
	statusF := r.URL.Query().Get("status")
	hostF := r.URL.Query().Get("host")
	filtered := filterAgents(agents, search, statusF, hostF)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.AgentsPage(filtered, search, statusF, hostF), "/agents")
}

// AgentsTablePartial renders only the table rows for HTMX partial updates.
func (h *HostsHandler) AgentsTablePartial(w http.ResponseWriter, r *http.Request) {
	agents := h.cache.GetAgents()
	search := r.URL.Query().Get("search")
	statusF := r.URL.Query().Get("status")
	hostF := r.URL.Query().Get("host")
	filtered := filterAgents(agents, search, statusF, hostF)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	for _, a := range filtered {
		renderTempl(w, r, templates.AgentRow(a), "/partials/agents/table")
	}
}

// filterAgents applies case-insensitive substring search on Name+Host,
// exact status match, and exact host match.
func filterAgents(agents []store.AgentRecord, search, status, host string) []store.AgentRecord {
	if search == "" && status == "" && host == "" {
		return agents
	}
	searchLower := strings.ToLower(search)
	out := make([]store.AgentRecord, 0, len(agents))
	for _, a := range agents {
		if search != "" {
			nameLower := strings.ToLower(a.Name)
			hostLower := strings.ToLower(a.Host)
			if !strings.Contains(nameLower, searchLower) && !strings.Contains(hostLower, searchLower) {
				continue
			}
		}
		if status != "" && a.Status != status {
			continue
		}
		if host != "" && a.Host != host {
			continue
		}
		out = append(out, a)
	}
	return out
}

// GrafanaPage renders the /grafana panel matrix page.
func (h *HostsHandler) GrafanaPage(w http.ResponseWriter, r *http.Request) {
	from := r.URL.Query().Get("from")
	if !validTimeRange.MatchString(from) {
		from = "now-1h"
	}
	to := r.URL.Query().Get("to")
	if !validTimeRange.MatchString(to) {
		to = "now"
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.GrafanaPage(grafana.KnownPanels, h.grafanaURL, from, to), "/grafana")
}

// NATSPage renders the /nats page with JetStream streams and connections.
func (h *HostsHandler) NATSPage(w http.ResponseWriter, r *http.Request) {
	streams := h.cache.GetNATSStreams()
	conns := h.cache.GetNATSConns()
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.NATSPage(streams, conns, h.natsDashURL, h.natsTopURL, h.natsMon), "/nats")
}

// NATSStreamsPartial renders only the streams table rows for HTMX partial updates.
func (h *HostsHandler) NATSStreamsPartial(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.NATSStreamRows(h.cache.GetNATSStreams()), "/partials/nats/streams")
}

// NATSConnsPartial renders only the connections table rows for HTMX partial updates.
func (h *HostsHandler) NATSConnsPartial(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.NATSConnRows(h.cache.GetNATSConns()), "/partials/nats/connections")
}

// MnemosynePage renders the /mnemosyne skill registry browser page.
func (h *HostsHandler) MnemosynePage(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	var skills []mnemosyne.Skill
	if h.mnemoReader != nil {
		var err error
		skills, err = h.mnemoReader.Skills()
		if err != nil {
			slog.Warn("mnemosyne: failed to load skills", "err", err)
		}
	}
	filtered := mnemosyne.Filter(skills, q)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.MnemosynePage(filtered, q), "/mnemosyne")
}
