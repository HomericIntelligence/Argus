package logsafe

import "testing"

func TestValueStripsControlCharacters(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"plain", "alice", "alice"},
		{"crlf", "a\r\nb", "ab"},
		{"newline", "a\nb", "ab"},
		{"carriage", "a\rb", "ab"},
		{"tab", "a\tb", "ab"},
		{"del", "a\x7fb", "ab"},
		{"all-control", "\x00\x01\x1f", ""},
		{"unicode-kept", "héllo→", "héllo→"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := Value(tc.in); got != tc.want {
				t.Errorf("Value(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}
