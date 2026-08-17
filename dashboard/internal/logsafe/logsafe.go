// Package logsafe sanitizes user-controlled values before they are logged,
// preventing CRLF/log-injection through structured logging sinks.
package logsafe

import "strings"

// Value strips control characters (including CR/LF used for log injection)
// from user-controlled values. slog escapes newlines in its text handler, but
// JSON/console handlers and downstream aggregators may not; sanitizing at the
// source keeps log lines single-line and attacker-controlled content inert.
func Value(v string) string {
	return strings.Map(func(r rune) rune {
		if r < 0x20 || r == 0x7f {
			return -1
		}
		return r
	}, v)
}
