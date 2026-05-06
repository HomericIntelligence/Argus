package mnemosyne

import (
	"strings"
	"testing"
)

// TestRenderMarkdown_StripsRawHTML asserts goldmark default behavior: raw
// HTML elements are stripped (replaced with HTML comments) so the rendered
// output cannot execute scripts, load remote iframes, or fire onerror
// handlers. The text *content* between tags may legitimately survive as
// plain text; the XSS defence is that the executable HTML SURFACE is gone.
//
// This is the load-bearing XSS defence for the Mnemosyne skill viewer — if
// anyone ever swaps in goldmark.WithUnsafe() every skill body becomes an XSS
// surface.
func TestRenderMarkdown_StripsRawHTML(t *testing.T) {
	cases := []struct {
		name string
		in   string
		// substrings that MUST NOT appear in the output — these are the
		// executable HTML surfaces, not the inner text.
		forbidden []string
	}{
		{
			name:      "script tag",
			in:        "Hello <script>alert(1)</script> world",
			forbidden: []string{"<script>", "</script>"},
		},
		{
			name:      "img onerror",
			in:        `Hello <img src=x onerror="alert(1)"> world`,
			forbidden: []string{"<img", "onerror="},
		},
		{
			name:      "iframe",
			in:        `<iframe src="evil.com"></iframe>`,
			forbidden: []string{"<iframe", "</iframe>"},
		},
		{
			name:      "raw style attr",
			in:        `<div style="background:url(javascript:alert(1))">x</div>`,
			forbidden: []string{"<div", "style=", "javascript:"},
		},
		{
			name:      "svg with onload",
			in:        `<svg onload="alert(1)"></svg>`,
			forbidden: []string{"<svg", "onload="},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got, err := RenderMarkdown(tc.in)
			if err != nil {
				t.Fatalf("RenderMarkdown: %v", err)
			}
			for _, sub := range tc.forbidden {
				if strings.Contains(got, sub) {
					t.Errorf("output must not contain executable HTML %q; got:\n%s", sub, got)
				}
			}
			// Positive assertion: goldmark replaces raw HTML with a comment
			// marker, which is the canonical signal that the sanitiser ran.
			if !strings.Contains(got, "raw HTML omitted") {
				t.Errorf("output should contain goldmark's `raw HTML omitted` marker; got:\n%s", got)
			}
		})
	}
}

// TestRenderMarkdown_SanitisesJavascriptLinks verifies goldmark's URL
// sanitiser strips javascript: from link/image URLs.
func TestRenderMarkdown_SanitisesJavascriptLinks(t *testing.T) {
	got, err := RenderMarkdown("[click](javascript:alert(1))")
	if err != nil {
		t.Fatalf("RenderMarkdown: %v", err)
	}
	if strings.Contains(got, "javascript:alert(1)") {
		t.Errorf("javascript: URL must be stripped/escaped; got:\n%s", got)
	}
}

// TestRenderMarkdown_StandardMarkdown verifies happy path renders work so
// the XSS defence isn't paying for itself by breaking the feature.
func TestRenderMarkdown_StandardMarkdown(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want []string
	}{
		{"heading", "# Title", []string{"<h1>", "Title", "</h1>"}},
		{"emphasis", "*emphasised*", []string{"<em>", "emphasised", "</em>"}},
		{"code", "Use `Run()` here", []string{"<code>", "Run()", "</code>"}},
		{"link", "[home](https://example.com)", []string{`<a href="https://example.com"`, "home"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got, err := RenderMarkdown(tc.in)
			if err != nil {
				t.Fatalf("RenderMarkdown: %v", err)
			}
			for _, sub := range tc.want {
				if !strings.Contains(got, sub) {
					t.Errorf("output missing %q; got:\n%s", sub, got)
				}
			}
		})
	}
}

// TestRenderMarkdown_Empty handles the edge case where a skill file's body
// is empty.
func TestRenderMarkdown_Empty(t *testing.T) {
	got, err := RenderMarkdown("")
	if err != nil {
		t.Fatalf("RenderMarkdown: %v", err)
	}
	if got != "" {
		t.Errorf("empty input should render to empty string; got %q", got)
	}
}
