package mnemosyne

import "strings"

// Filter returns the subset of skills whose Name, Description, Category, or
// Tags contain every whitespace-separated token in query (case-insensitive).
// An empty query returns the input slice unchanged.
func Filter(skills []Skill, query string) []Skill {
	if query == "" {
		return skills
	}
	tokens := strings.Fields(strings.ToLower(query))
	var out []Skill
	for _, s := range skills {
		haystack := strings.ToLower(s.Name + " " + s.Description + " " + s.Category + " " + strings.Join(s.Tags, " "))
		match := true
		for _, t := range tokens {
			if !strings.Contains(haystack, t) {
				match = false
				break
			}
		}
		if match {
			out = append(out, s)
		}
	}
	return out
}
