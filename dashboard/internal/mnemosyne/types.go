package mnemosyne

// Skill is a parsed Mnemosyne skill markdown file: YAML frontmatter fields
// plus the raw markdown body that followed the closing fence.
type Skill struct {
	Name         string   `yaml:"name"`
	Description  string   `yaml:"description"`
	Category     string   `yaml:"category"`
	Tags         []string `yaml:"tags"`
	Version      string   `yaml:"version"`
	Verification string   `yaml:"verification"`
	FilePath     string
	Body         string // raw markdown body after frontmatter
}
