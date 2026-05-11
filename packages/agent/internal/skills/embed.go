// Package skills embeds the SKILL.md files into the binary so
// `runtm skills install` can write them to disk without a network fetch.
package skills

import "embed"

//go:embed files/*
var FS embed.FS

// Dir is the subdirectory inside the embedded FS.
const Dir = "files"
