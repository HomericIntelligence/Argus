package handlers

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/HomericIntelligence/atlas/internal/store"
	"github.com/HomericIntelligence/atlas/web/templates"
)

// AgentDetail handles GET /agents/{id}.
// It looks up the agent in the cache, finds any in-progress task assigned to it,
// retrieves the per-agent event history, and renders the detail page.
func (h *HostsHandler) AgentDetail(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	agents := h.cache.GetAgents()
	var found *store.AgentRecord
	for i := range agents {
		if agents[i].ID == id {
			found = &agents[i]
			break
		}
	}
	if found == nil {
		http.NotFound(w, r)
		return
	}

	// Find the current in-progress task assigned to this agent (if any).
	var task *store.TaskRecord
	for _, t := range h.cache.GetTasks() {
		if t.AssigneeID == id && t.Status == "in_progress" {
			tc := t
			task = &tc
			break
		}
	}

	history := h.cache.GetAgentEvents(id)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.AgentDetailPage(*found, task, history), "/agents/{id}")
}

// TaskDetail handles GET /tasks/{id}.
// It looks up the task in the cache, retrieves the event history for the
// assigned agent, and renders the task detail page.
func (h *HostsHandler) TaskDetail(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	tasks := h.cache.GetTasks()
	var found *store.TaskRecord
	for i := range tasks {
		if tasks[i].ID == id {
			found = &tasks[i]
			break
		}
	}
	if found == nil {
		http.NotFound(w, r)
		return
	}

	history := h.cache.GetAgentEvents(found.AssigneeID)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.TaskDetailPage(*found, history), "/tasks/{id}")
}
