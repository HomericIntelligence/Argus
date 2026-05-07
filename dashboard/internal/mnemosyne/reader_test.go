package mnemosyne

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fixtureSkill = `---
name: nats-publish
description: Publish a message to a NATS subject
category: messaging
tags:
  - nats
  - messaging
version: "1.0"
verification: verified-ci
---
## Usage

Publish a message to a NATS subject using the CLI.
`

const fixtureSkill2 = `---
name: http-request
description: Make an HTTP request to a remote endpoint
category: networking
tags:
  - http
  - networking
version: "2.1"
verification: verified-local
---
## Usage

Send an HTTP GET request to a remote endpoint.
`

// Test 1: Load fixture .md file; assert Name, Description, Version, Tags populated.
func TestLoadSkill(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "nats-publish.md"), []byte(fixtureSkill), 0o600); err != nil {
		t.Fatal(err)
	}

	r := NewReader(dir)
	skills, err := r.Skills()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(skills) != 1 {
		t.Fatalf("expected 1 skill, got %d", len(skills))
	}
	s := skills[0]
	if s.Name != "nats-publish" {
		t.Errorf("expected Name=%q got %q", "nats-publish", s.Name)
	}
	if s.Description != "Publish a message to a NATS subject" {
		t.Errorf("unexpected Description: %q", s.Description)
	}
	if s.Version != "1.0" {
		t.Errorf("expected Version=%q got %q", "1.0", s.Version)
	}
	if len(s.Tags) == 0 || s.Tags[0] != "nats" {
		t.Errorf("expected Tags to include 'nats', got %v", s.Tags)
	}
}

// Test 2: Filter("nats") returns only NATS-tagged skills; Filter("") returns all.
func TestFilter(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "nats-publish.md"), []byte(fixtureSkill), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "http-request.md"), []byte(fixtureSkill2), 0o600); err != nil {
		t.Fatal(err)
	}

	r := NewReader(dir)
	all, err := r.Skills()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(all) != 2 {
		t.Fatalf("expected 2 skills, got %d", len(all))
	}

	// Filter("") returns all
	empty := Filter(all, "")
	if len(empty) != 2 {
		t.Errorf("Filter('') should return all skills, got %d", len(empty))
	}

	// Filter("nats") returns only NATS-tagged skill
	filtered := Filter(all, "nats")
	if len(filtered) != 1 {
		t.Errorf("Filter('nats') should return 1 skill, got %d", len(filtered))
	}
	if filtered[0].Name != "nats-publish" {
		t.Errorf("expected nats-publish, got %q", filtered[0].Name)
	}
}

// Test 3: HTML in skill body rendered as escaped text, not raw HTML.
// e.g. body contains "<script>alert(1)</script>" → rendered as "&lt;script&gt;..."
func TestRenderMarkdownXSS(t *testing.T) {
	body := "<script>alert(1)</script>\n"
	html, err := RenderMarkdown(body)
	if err != nil {
		t.Fatalf("unexpected render error: %v", err)
	}
	if strings.Contains(html, "<script>") {
		t.Errorf("raw <script> tag should not appear in rendered output, got: %q", html)
	}
	// goldmark strips raw HTML by default (Unsafe=false), so the script tag is omitted entirely
	// or rendered as escaped text - either way it must not be executable
	if strings.Contains(html, "alert(1)") && strings.Contains(html, "<script>") {
		t.Errorf("XSS vector present in output: %q", html)
	}
}

// Test 4: missing dir returns empty slice, not error.
func TestMissingDirReturnsEmpty(t *testing.T) {
	r := NewReader("/nonexistent/path/that/does/not/exist")
	skills, err := r.Skills()
	if err != nil {
		t.Fatalf("expected nil error for missing dir, got: %v", err)
	}
	if len(skills) != 0 {
		t.Errorf("expected 0 skills for missing dir, got %d", len(skills))
	}
}
