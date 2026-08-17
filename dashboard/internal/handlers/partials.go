package handlers

import (
	"io"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/HomericIntelligence/atlas/internal/logsafe"
	"github.com/HomericIntelligence/atlas/internal/mnemosyne"
	"github.com/HomericIntelligence/atlas/web/templates"
)

// MnemosyneSearch renders the skill list partial for HTMX search updates.
// GET /partials/mnemosyne/search?q=
func (h *HostsHandler) MnemosyneSearch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	var skills []mnemosyne.Skill
	if h.mnemoReader != nil {
		var err error
		skills, err = h.mnemoReader.Skills()
		if err != nil {
			slog.Warn("mnemosyne: failed to load skills", "err", err, "route", "/partials/mnemosyne/search")
		}
	}
	filtered := mnemosyne.Filter(skills, q)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	renderTempl(w, r, templates.SkillList(filtered), "/partials/mnemosyne/search")
}

// MnemosyneSkillBody renders the markdown body of a skill as HTML.
// GET /partials/mnemosyne/skill/{name}
func (h *HostsHandler) MnemosyneSkillBody(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	if h.mnemoReader == nil {
		http.NotFound(w, r)
		return
	}
	skills, err := h.mnemoReader.Skills()
	if err != nil {
		slog.Warn("mnemosyne: failed to load skills", "err", err, "route", "/partials/mnemosyne/skill/{name}")
		http.Error(w, "skills unavailable", http.StatusServiceUnavailable)
		return
	}
	for _, s := range skills {
		if s.Name == name {
			html, err := mnemosyne.RenderMarkdown(s.Body)
			if err != nil {
				slog.Warn("mnemosyne: render failed", "err", err, "skill", logsafe.Value(name))
				http.Error(w, "render error", http.StatusInternalServerError)
				return
			}
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			// Safe: goldmark renders with Unsafe=false (raw HTML stripped).
			// We use io.WriteString rather than fmt.Fprint so the bytes-written
			// error is explicit; client-disconnect during a partial is normal
			// and demoted to debug.
			if _, werr := io.WriteString(w, html); werr != nil && !isClientDisconnect(werr) {
				slog.Warn("mnemosyne: write skill body failed", "err", werr, "skill", logsafe.Value(name))
			}
			return
		}
	}
	http.NotFound(w, r)
}
