package server

import (
	"net/http"
	"time"

	"github.com/go-chi/httprate"
)

// rateLimit returns a chi middleware that allows at most reqPerMin
// requests per minute from each unique remote IP. If reqPerMin is 0
// the middleware is a no-op pass-through (operator escape hatch via
// ATLAS_RATE_LIMIT_PER_MIN=0 / ATLAS_LIVEZ_RATE_LIMIT_PER_MIN=0).
//
// Closes the §8 audit MAJOR finding "no rate-limiting middleware."
// Combined with the previous-PR fixes (Server.WriteTimeout=0 stays
// for SSE, but per-IP request rate is now bounded; auth failures are
// logged so brute-force attempts are visible) this removes the
// trivial unauthenticated-DoS vector against /readyz, /metrics,
// /events, and the UI.
//
// Keying is by RemoteAddr (httprate.KeyByIP). RemoteAddr is the
// kernel-visible peer address — trustworthy as long as Atlas is not
// behind a proxy that rewrites it. PR #461 already removes
// middleware.RealIP for exactly this reason; if a future deployment
// runs Atlas behind nginx/Caddy/etc., the right fix is to introduce
// a trusted-proxy gate (ATLAS_TRUST_PROXY) that conditionally
// re-enables RealIP — both the access log, the auth-failure event
// stream, and this rate limiter would then key on the proxy-asserted
// client IP instead of the proxy itself.
func rateLimit(reqPerMin int) func(http.Handler) http.Handler {
	if reqPerMin <= 0 {
		return func(next http.Handler) http.Handler { return next }
	}
	return httprate.Limit(
		reqPerMin,
		time.Minute,
		httprate.WithKeyByIP(),
	)
}
