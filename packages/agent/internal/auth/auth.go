// Package auth resolves API credentials and base URL for the runtm CLI.
//
// Resolution order matches the existing Python CLI so customers who already ran
// `runtm login` work without reconfiguration:
//
//  1. RUNTM_API_KEY environment variable
//  2. ~/.runtm/credentials file (mode 0600, written by the pip CLI)
//
// Base URL: RUNTM_API_URL env -> ~/.runtm/config.yaml api_url -> default.
package auth

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// DefaultAPIURL is the production cloud endpoint. The existing pip CLI also
// defaults to this value.
const DefaultAPIURL = "https://app.runtm.com/api"

// ErrNoCredentials means we could not locate an API key in env or on disk.
var ErrNoCredentials = errors.New("no API key found. Set RUNTM_API_KEY or run: runtm login (pip CLI)")

// Credentials holds resolved authentication context for a CLI invocation.
type Credentials struct {
	APIKey         string
	APIURL         string
	OrganizationID string
	Source         string // "env" or "file"
}

// Load resolves credentials using env vars and the on-disk config file.
// The caller may pass override values (typically from --api-url / --org flags).
func Load(apiURLOverride, orgOverride string) (*Credentials, error) {
	key, source := readKey()
	if key == "" {
		return nil, ErrNoCredentials
	}

	apiURL := strings.TrimRight(resolveAPIURL(apiURLOverride), "/")
	orgID := resolveOrg(orgOverride)

	return &Credentials{
		APIKey:         key,
		APIURL:         apiURL,
		OrganizationID: orgID,
		Source:         source,
	}, nil
}

// LoadOptional returns credentials when present and nil when missing, without
// erroring. Useful for `runtm auth status` which should not fail when unset.
func LoadOptional(apiURLOverride, orgOverride string) *Credentials {
	creds, err := Load(apiURLOverride, orgOverride)
	if err != nil {
		return nil
	}
	return creds
}

func readKey() (string, string) {
	if v := strings.TrimSpace(os.Getenv("RUNTM_API_KEY")); v != "" {
		return v, "env"
	}

	path, err := credentialsPath()
	if err != nil {
		return "", ""
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", ""
	}
	if token := strings.TrimSpace(string(data)); token != "" {
		return token, "file"
	}
	return "", ""
}

func resolveAPIURL(override string) string {
	if override != "" {
		return override
	}
	if v := strings.TrimSpace(os.Getenv("RUNTM_API_URL")); v != "" {
		return v
	}
	if v := readConfigAPIURL(); v != "" {
		return v
	}
	return DefaultAPIURL
}

func resolveOrg(override string) string {
	if override != "" {
		return override
	}
	return strings.TrimSpace(os.Getenv("RUNTM_ORG_ID"))
}

func credentialsPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("resolve home dir: %w", err)
	}
	return filepath.Join(home, ".runtm", "credentials"), nil
}

// readConfigAPIURL does a minimal scan of ~/.runtm/config.yaml looking for the
// `api_url:` key. We intentionally avoid pulling in a YAML dependency for a
// single-key lookup -- the pip CLI writes the file in a stable format.
func readConfigAPIURL() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	path := filepath.Join(home, ".runtm", "config.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "api_url:") {
			continue
		}
		value := strings.TrimSpace(strings.TrimPrefix(line, "api_url:"))
		value = strings.Trim(value, "\"'")
		if value != "" {
			return value
		}
	}
	return ""
}
