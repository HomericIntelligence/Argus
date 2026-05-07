package poller

import (
	"context"
	"log/slog"
	"net/http"
	"time"

	"github.com/HomericIntelligence/atlas/internal/config"
	"github.com/HomericIntelligence/atlas/internal/store"
)

// agentAPIRecord is the camelCase JSON shape returned by Agamemnon's /v1/agents.
type agentAPIRecord struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Host      string    `json:"host"`
	Status    string    `json:"status"`
	UpdatedAt time.Time `json:"updatedAt"`
}

// taskAPIRecord is the camelCase JSON shape returned by Agamemnon's /v1/tasks.
type taskAPIRecord struct {
	ID         string    `json:"id"`
	TeamID     string    `json:"teamId"`
	Subject    string    `json:"subject"`
	Status     string    `json:"status"`
	AssigneeID string    `json:"assigneeAgentId"`
	CreatedAt  time.Time `json:"createdAt"`
	UpdatedAt  time.Time `json:"updatedAt"`
}

// tasksAPIResponse is the envelope returned by Agamemnon's /v1/tasks.
type tasksAPIResponse struct {
	Tasks []taskAPIRecord `json:"tasks"`
}

// AgamemnonPoller polls the Agamemnon service for agent and task data.
type AgamemnonPoller struct {
	*base
	cache *store.Cache
	url   string
}

// NewAgamemnonPoller constructs an AgamemnonPoller using the upstream HTTP
// timeout from cfg.UpstreamTimeout (ATLAS_UPSTREAM_TIMEOUT, default 3s).
func NewAgamemnonPoller(cfg *config.Config, cache *store.Cache) *AgamemnonPoller {
	timeout := cfg.UpstreamTimeout
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	return &AgamemnonPoller{
		base:  newBase("agamemnon", &http.Client{Timeout: timeout}),
		cache: cache,
		url:   cfg.AgamemnonURL,
	}
}

// Start runs the poller in a ticker loop until ctx is cancelled.
func (p *AgamemnonPoller) Start(ctx context.Context, interval time.Duration) {
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

// fetch retrieves agents and tasks from Agamemnon and updates the cache.
// On any error it logs a warning and leaves the cache unchanged. Both
// /v1/agents and /v1/tasks must succeed for the poll cycle to count as
// successful — a partial fetch leaves the cache in a mixed state and the
// /readyz aggregator should consider it not-ready.
func (p *AgamemnonPoller) fetch(ctx context.Context) {
	start := time.Now()
	err := p.fetchInner(ctx)
	if err != nil {
		slog.Warn("agamemnon poller: fetch failed", "err", err)
	}
	p.recordResult(err, time.Since(start))
}

func (p *AgamemnonPoller) fetchInner(ctx context.Context) error {
	// Fetch agents.
	var rawAgents []agentAPIRecord
	if err := p.getJSON(ctx, p.url+"/v1/agents", &rawAgents); err != nil {
		return err
	}

	agents := make([]store.AgentRecord, len(rawAgents))
	for i, a := range rawAgents {
		agents[i] = store.AgentRecord{
			ID:        a.ID,
			Name:      a.Name,
			Host:      a.Host,
			Status:    a.Status,
			UpdatedAt: a.UpdatedAt,
		}
	}
	p.cache.SetAgents(agents)

	// Fetch tasks.
	var envelope tasksAPIResponse
	if err := p.getJSON(ctx, p.url+"/v1/tasks", &envelope); err != nil {
		return err
	}

	tasks := make([]store.TaskRecord, len(envelope.Tasks))
	for i, t := range envelope.Tasks {
		tasks[i] = store.TaskRecord{
			ID:         t.ID,
			TeamID:     t.TeamID,
			Subject:    t.Subject,
			Status:     t.Status,
			AssigneeID: t.AssigneeID,
			CreatedAt:  t.CreatedAt,
			UpdatedAt:  t.UpdatedAt,
		}
	}
	p.cache.SetTasks(tasks)
	return nil
}
