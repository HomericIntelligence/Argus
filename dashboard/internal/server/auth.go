package server

import (
	"crypto/subtle"
	"encoding/base64"
	"log/slog"
	"net/http"
	"strings"

	"github.com/HomericIntelligence/atlas/internal/logsafe"
)

// AuthMode represents the authentication scheme for the Atlas dashboard.
type AuthMode string

// AuthMode constants are the canonical values accepted by ATLAS_AUTH_MODE.
const (
	AuthNone   AuthMode = "none"
	AuthBasic  AuthMode = "basic"
	AuthBearer AuthMode = "bearer"
)

// Middleware returns a Chi middleware that enforces the configured auth mode.
//
//   - none: no-op passthrough; all requests are allowed.
//   - basic: validates Authorization: Basic <base64(user:pass)>.
//     On failure returns 401 with WWW-Authenticate: Basic realm="Atlas".
//   - bearer: validates Authorization: Bearer <token> OR ?token=<token>.
//     The query-param fallback exists for EventSource / SSE clients that cannot
//     set custom request headers (Accept: text/event-stream).
//     On failure returns 401.
//
// Unknown modes (anything other than the three above) fail closed: every
// request is rejected with 401. config.Validate is the canonical guard
// against this — it normalizes ATLAS_AUTH_MODE to lowercase and rejects
// unknown values at startup — but the middleware also refuses to fail open
// as a defense-in-depth measure.
func Middleware(mode AuthMode, user, pass, bearerToken string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch mode {
			case AuthNone:
				// Explicit pass-through. Operators must opt in via
				// ATLAS_AUTH_MODE=none and config.Validate logs a warning.
			case AuthBasic:
				if !checkBasic(r, user, pass) {
					logAuthFailure(r, "basic", basicFailureReason(r))
					w.Header().Set("WWW-Authenticate", `Basic realm="Atlas"`)
					http.Error(w, "Unauthorized", http.StatusUnauthorized)
					return
				}
			case AuthBearer:
				if !checkBearer(r, bearerToken) {
					logAuthFailure(r, "bearer", bearerFailureReason(r, bearerToken))
					http.Error(w, "Unauthorized", http.StatusUnauthorized)
					return
				}
			default:
				// Fail closed on any unknown mode. config.Validate should
				// have rejected this at startup; reaching this branch means
				// something bypassed Validate (e.g. a future code path that
				// constructs Server with a hand-built Config), and we'd
				// rather 401 every request than open the dashboard.
				logAuthFailure(r, string(mode), "unknown-mode")
				http.Error(w, "Unauthorized", http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// logAuthFailure emits one slog.Warn per failed auth event so security
// monitoring (Loki alert rules, SIEM ingest) can detect scanners,
// brute-force attempts, and misconfigured clients. The line carries
// enough context for triage but NEVER the offered credential or token —
// see also the access_log middleware (path-only logging) which already
// strips query strings to keep SSE bearer tokens out of stdout.
//
// Fields:
//   - mode    : the configured auth scheme (basic / bearer / unknown-mode-string)
//   - reason  : a stable discriminator (missing-header / wrong-creds /
//     wrong-token / empty-configured-token / unknown-mode)
//   - method  : HTTP method, useful for distinguishing browsers from CLIs
//   - path    : URL path only (no query string — see access_log for why)
//   - remote  : r.RemoteAddr; reflects middleware.RealIP rewrites if any
//
// At scanner volumes this fires often. That is the point. If a future
// operator wants throttling, the right place to add it is here, not by
// reverting to silent 401s.
func logAuthFailure(r *http.Request, mode, reason string) {
	slog.Default().Warn(
		"auth failure",
		"mode", mode,
		"reason", reason,
		"method", r.Method,
		"path", logsafe.Value(r.URL.Path),
		"remote", logsafe.Value(r.RemoteAddr),
	)
}

// basicFailureReason classifies why a basic-auth check failed without
// disclosing whether the username or password was the wrong half. The
// distinction between a missing/malformed header and a comparison
// mismatch is operationally useful (scanners vs typos) and not a
// confidentiality leak.
func basicFailureReason(r *http.Request) string {
	authHeader := r.Header.Get("Authorization")
	if authHeader == "" {
		return "missing-header"
	}
	if !strings.HasPrefix(authHeader, "Basic ") {
		return "wrong-scheme"
	}
	encoded := strings.TrimPrefix(authHeader, "Basic ")
	if _, err := base64.StdEncoding.DecodeString(encoded); err != nil {
		return "malformed-base64"
	}
	return "wrong-creds"
}

// bearerFailureReason classifies why a bearer check failed. Mirrors the
// same shape as basicFailureReason. The "empty-configured-token" reason
// is distinct because it points at a server-side misconfiguration rather
// than a client-side problem (config.Validate rejects this at startup,
// but the runtime guard in checkBearer also handles it; if this reason
// ever fires in production, something bypassed Validate — investigate).
func bearerFailureReason(r *http.Request, configuredToken string) string {
	if configuredToken == "" {
		return "empty-configured-token"
	}
	authHeader := r.Header.Get("Authorization")
	hasHeader := strings.HasPrefix(authHeader, "Bearer ")
	hasQuery := r.URL.Query().Get("token") != ""
	if !hasHeader && !hasQuery {
		return "missing-header"
	}
	return "wrong-token"
}

// checkBasic validates an HTTP Basic auth header against the expected credentials.
// Comparisons use constant-time equality to prevent timing attacks.
func checkBasic(r *http.Request, user, pass string) bool {
	authHeader := r.Header.Get("Authorization")
	if !strings.HasPrefix(authHeader, "Basic ") {
		return false
	}
	encoded := strings.TrimPrefix(authHeader, "Basic ")
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return false
	}
	parts := strings.SplitN(string(decoded), ":", 2)
	if len(parts) != 2 {
		return false
	}
	userOK := subtle.ConstantTimeCompare([]byte(parts[0]), []byte(user)) == 1
	passOK := subtle.ConstantTimeCompare([]byte(parts[1]), []byte(pass)) == 1
	return userOK && passOK
}

// checkBearer validates a Bearer token from either the Authorization header or
// the ?token= query parameter (for SSE / EventSource compatibility).
// An empty configured token always rejects — prevents accidental open access
// when ATLAS_AUTH_BEARER_TOKEN is unset. Comparisons use constant-time equality
// to prevent timing attacks.
func checkBearer(r *http.Request, token string) bool {
	if token == "" {
		return false
	}
	// Check Authorization: Bearer <token> header first.
	authHeader := r.Header.Get("Authorization")
	if strings.HasPrefix(authHeader, "Bearer ") {
		provided := strings.TrimPrefix(authHeader, "Bearer ")
		return subtle.ConstantTimeCompare([]byte(provided), []byte(token)) == 1
	}
	// Fall back to ?token= query parameter (EventSource compat).
	if qt := r.URL.Query().Get("token"); qt != "" {
		return subtle.ConstantTimeCompare([]byte(qt), []byte(token)) == 1
	}
	return false
}
