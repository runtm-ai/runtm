package auth

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadFromEnv(t *testing.T) {
	t.Setenv("RUNTM_API_KEY", "runtm_sk_test_env")
	t.Setenv("RUNTM_API_URL", "http://localhost:8081/api")
	t.Setenv("RUNTM_ORG_ID", "org_env")

	creds, err := Load("", "")
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if creds.APIKey != "runtm_sk_test_env" {
		t.Errorf("APIKey = %q, want runtm_sk_test_env", creds.APIKey)
	}
	if creds.APIURL != "http://localhost:8081/api" {
		t.Errorf("APIURL = %q, want http://localhost:8081/api", creds.APIURL)
	}
	if creds.OrganizationID != "org_env" {
		t.Errorf("OrganizationID = %q, want org_env", creds.OrganizationID)
	}
	if creds.Source != "env" {
		t.Errorf("Source = %q, want env", creds.Source)
	}
}

func TestLoadFromFile(t *testing.T) {
	tempHome := t.TempDir()
	t.Setenv("HOME", tempHome)
	// Unset env so file is used.
	t.Setenv("RUNTM_API_KEY", "")
	t.Setenv("RUNTM_API_URL", "")
	t.Setenv("RUNTM_ORG_ID", "")

	dir := filepath.Join(tempHome, ".runtm")
	if err := os.Mkdir(dir, 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "credentials"), []byte("runtm_sk_file_value\n"), 0o600); err != nil {
		t.Fatalf("write credentials: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.yaml"), []byte("api_url: http://localhost:9000/api\n"), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}

	creds, err := Load("", "")
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if creds.APIKey != "runtm_sk_file_value" {
		t.Errorf("APIKey = %q, want runtm_sk_file_value", creds.APIKey)
	}
	if creds.APIURL != "http://localhost:9000/api" {
		t.Errorf("APIURL = %q, want http://localhost:9000/api", creds.APIURL)
	}
	if creds.Source != "file" {
		t.Errorf("Source = %q, want file", creds.Source)
	}
}

func TestLoadNoCredentials(t *testing.T) {
	tempHome := t.TempDir()
	t.Setenv("HOME", tempHome)
	t.Setenv("RUNTM_API_KEY", "")
	t.Setenv("RUNTM_API_URL", "")

	_, err := Load("", "")
	if !errors.Is(err, ErrNoCredentials) {
		t.Fatalf("Load returned %v, want ErrNoCredentials", err)
	}
}

func TestLoadOverridesWinOverEnv(t *testing.T) {
	t.Setenv("RUNTM_API_KEY", "runtm_sk_test_env")
	t.Setenv("RUNTM_API_URL", "http://env-url/api")
	t.Setenv("RUNTM_ORG_ID", "org_env")

	creds, err := Load("http://flag-url/api", "org_flag")
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if creds.APIURL != "http://flag-url/api" {
		t.Errorf("APIURL = %q, want flag value", creds.APIURL)
	}
	if creds.OrganizationID != "org_flag" {
		t.Errorf("OrganizationID = %q, want flag value", creds.OrganizationID)
	}
}

func TestLoadDefaultURL(t *testing.T) {
	tempHome := t.TempDir()
	t.Setenv("HOME", tempHome)
	t.Setenv("RUNTM_API_KEY", "runtm_sk_only")
	t.Setenv("RUNTM_API_URL", "")

	creds, err := Load("", "")
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if creds.APIURL != DefaultAPIURL {
		t.Errorf("APIURL = %q, want default %q", creds.APIURL, DefaultAPIURL)
	}
}
