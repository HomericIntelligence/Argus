// Package grafana describes the Grafana panels Atlas embeds via d-solo
// iframes and helpers for constructing their URLs.
package grafana

// Panel describes a single Grafana panel that Atlas embeds via d-solo iframe.
type Panel struct {
	DashUID string
	Slug    string
	PanelID int
	Title   string
}

// KnownPanels is the static set of panels surfaced on the /grafana page.
var KnownPanels = []Panel{
	{DashUID: "agent-health", Slug: "agent-health", PanelID: 1, Title: "Agent Status"},
	{DashUID: "agent-health", Slug: "agent-health", PanelID: 2, Title: "Agent Count"},
	{DashUID: "argus-health", Slug: "argus-health", PanelID: 1, Title: "Argus Health"},
	{DashUID: "loki-explorer", Slug: "loki-explorer", PanelID: 1, Title: "Logs"},
	{DashUID: "nats-events", Slug: "nats-events", PanelID: 1, Title: "NATS Events/s"},
	{DashUID: "task-throughput", Slug: "task-throughput", PanelID: 1, Title: "Task Funnel"},
	{DashUID: "task-throughput", Slug: "task-throughput", PanelID: 2, Title: "Task Rate"},
	{DashUID: "agent-health", Slug: "agent-health", PanelID: 3, Title: "Agent Timeline"},
}
