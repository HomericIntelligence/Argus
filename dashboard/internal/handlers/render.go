package handlers

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"syscall"
)

// templComponent matches the subset of templ.Component we need without
// importing the templ runtime package directly here. Every templ-generated
// _templ.go produces values that implement this method.
type templComponent interface {
	Render(ctx context.Context, w io.Writer) error
}

// renderTempl renders c into w and logs render failures. By the time we call
// Render the headers are already written and committed, so we cannot recover
// with a clean http.Error — the client may see a truncated page. This helper
// captures the structured log we want plus distinguishes ordinary client
// disconnects (broken pipe / reset) from genuine template errors so operators
// don't see a flood of WARN-level noise on healthy traffic.
func renderTempl(w http.ResponseWriter, r *http.Request, c templComponent, route string) {
	if err := c.Render(r.Context(), w); err != nil {
		// Client-disconnect family — extremely common on SSE-adjacent
		// pages and HTMX swaps when the user navigates away mid-flush.
		// Demote to debug so /var/log doesn't drown in noise.
		if isClientDisconnect(err) {
			slog.Debug("template render: client disconnect", "route", route, "err", err)
			return
		}
		slog.Warn("template render failed", "route", route, "err", err)
	}
}

func isClientDisconnect(err error) bool {
	switch {
	case errors.Is(err, syscall.EPIPE),
		errors.Is(err, syscall.ECONNRESET),
		errors.Is(err, context.Canceled),
		errors.Is(err, context.DeadlineExceeded):
		return true
	}
	return false
}
