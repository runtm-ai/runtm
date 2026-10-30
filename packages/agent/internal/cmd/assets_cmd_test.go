package cmd

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAssetsSubcommandsRegistered(t *testing.T) {
	assets := findSub(t, NewRootCommand(), "assets")
	for _, name := range []string{"upload", "list", "get", "publish", "unpublish", "delete"} {
		if !hasSub(assets, name) {
			t.Errorf("assets is missing %q", name)
		}
	}
}

func writeAsset(t *testing.T, name string, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func apiPath(r *http.Request) string {
	return strings.TrimPrefix(r.URL.Path, "/cloud")
}

// uploadServer plays the API and the bucket: start, PUT to the signed URL,
// complete. It records each step as "METHOD path".
func uploadServer(t *testing.T, base string, steps *[]string, gotBody *map[string]any, gotPut *[]byte) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		*steps = append(*steps, r.Method+" "+apiPath(r))
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && apiPath(r) == base:
			_ = json.NewDecoder(r.Body).Decode(gotBody)
			_, _ = io.WriteString(w, `{"asset":{"id":"a-1","status":"pending"},"upload":{"url":"http://`+r.Host+`/bucket/a-1?sig=x","method":"PUT","headers":{"Content-Type":"application/pdf"}}}`)
		case r.Method == http.MethodPut && r.URL.Path == "/bucket/a-1":
			if r.Header.Get("Content-Type") != "application/pdf" {
				t.Errorf("PUT Content-Type = %q", r.Header.Get("Content-Type"))
			}
			if r.ContentLength <= 0 {
				t.Errorf("PUT ContentLength = %d, want the file size", r.ContentLength)
			}
			*gotPut, _ = io.ReadAll(r.Body)
		case r.Method == http.MethodPost && apiPath(r) == base+"/a-1/complete":
			_, _ = io.WriteString(w, `{"id":"a-1","status":"ready","url":"https://app.test/assets/a-1?org=org-1"}`)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}
}

func TestAssetsUploadInASessionStreamsToTheSignedURL(t *testing.T) {
	data := []byte("%PDF-1.7 one-pager")
	path := writeAsset(t, "kushki.pdf", data)
	var steps []string
	var body map[string]any
	var put []byte
	rt, stdout := shareRuntime(t, uploadServer(t, "/v0/sessions/ses-1/assets", &steps, &body, &put))
	t.Setenv("RUNTM_SESSION_ID", "ses-1")

	cmd := newAssetsUpload(rt)
	cmd.SetArgs([]string{path})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("upload: %v", err)
	}

	want := []string{
		"POST /v0/sessions/ses-1/assets",
		"PUT /bucket/a-1",
		"POST /v0/sessions/ses-1/assets/a-1/complete",
	}
	if strings.Join(steps, "\n") != strings.Join(want, "\n") {
		t.Fatalf("steps = %v, want %v", steps, want)
	}
	sum := sha256.Sum256(data)
	if body["filename"] != "kushki.pdf" || body["size_bytes"] != float64(len(data)) || body["sha256"] != hex.EncodeToString(sum[:]) {
		t.Errorf("start body = %v", body)
	}
	if !bytes.Equal(put, data) {
		t.Errorf("PUT body = %q, want the file", put)
	}
	if !strings.Contains(stdout.String(), `"status": "ready"`) {
		t.Errorf("stdout = %s, want the ready asset", stdout.String())
	}
}

func TestAssetsUploadOutsideASessionUsesTheOrgRoutes(t *testing.T) {
	path := writeAsset(t, "local-name.pdf", []byte("%PDF"))
	var steps []string
	var body map[string]any
	var put []byte
	rt, _ := shareRuntime(t, uploadServer(t, "/assets", &steps, &body, &put))
	t.Setenv("RUNTM_SESSION_ID", "")
	t.Setenv("RUNTM_ORG_ID", "org-1")

	cmd := newAssetsUpload(rt)
	cmd.SetArgs([]string{path, "--name", "deck.pdf"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("upload: %v", err)
	}

	if len(steps) != 3 || steps[0] != "POST /assets" || steps[2] != "POST /assets/a-1/complete" {
		t.Fatalf("steps = %v", steps)
	}
	if body["filename"] != "deck.pdf" {
		t.Errorf("filename = %v, want the --name override", body["filename"])
	}
}

func TestAssetsUploadStopsWhenStorageRefusesThePut(t *testing.T) {
	path := writeAsset(t, "kushki.pdf", []byte("%PDF"))
	var steps []string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		steps = append(steps, r.Method+" "+apiPath(r))
		if r.Method == http.MethodPut {
			w.WriteHeader(http.StatusForbidden)
			_, _ = io.WriteString(w, "SignatureDoesNotMatch")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"asset":{"id":"a-1"},"upload":{"url":"http://`+r.Host+`/bucket/a-1","headers":{}}}`)
	})
	t.Setenv("RUNTM_SESSION_ID", "ses-1")

	cmd := newAssetsUpload(rt)
	cmd.SetArgs([]string{path})
	err := cmd.Execute()

	if err == nil || !strings.Contains(err.Error(), "status 403: SignatureDoesNotMatch") {
		t.Fatalf("err = %v, want the storage refusal", err)
	}
	if len(steps) != 2 {
		t.Errorf("steps = %v, want no complete call after a failed PUT", steps)
	}
}

func TestAssetsUploadRefusesEmptyFilesAndDirectories(t *testing.T) {
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
	})
	t.Setenv("RUNTM_SESSION_ID", "ses-1")

	for _, path := range []string{writeAsset(t, "empty.pdf", nil), t.TempDir()} {
		cmd := newAssetsUpload(rt)
		cmd.SetArgs([]string{path})
		if err := cmd.Execute(); err == nil {
			t.Errorf("upload %s: want an error", path)
		}
	}
}

func TestAssetsPublishInASessionAsksForApproval(t *testing.T) {
	var gotPath string
	var body map[string]any
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath = apiPath(r)
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"status":"pending","approval_id":"ap-1","assets":[{"id":"a-1","visibility":"private"}]}`)
	})
	t.Setenv("RUNTM_SESSION_ID", "ses-1")

	cmd := newAssetsPublish(rt)
	cmd.SetArgs([]string{"a-1", "a-2", "--note", "Kushki follow-up"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("publish: %v", err)
	}

	if gotPath != "/v0/sessions/ses-1/assets/publish" {
		t.Errorf("path = %q", gotPath)
	}
	if ids, _ := body["asset_ids"].([]any); len(ids) != 2 || body["note"] != "Kushki follow-up" {
		t.Errorf("body = %v", body)
	}
	var out map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil {
		t.Fatalf("stdout is not JSON: %s", stdout.String())
	}
	if out["approval_id"] != "ap-1" || !strings.Contains(out["next"].(string), "End your turn") {
		t.Errorf("stdout = %v, want the approval and what to do next", out)
	}
}

func TestAssetsPublishOutsideASessionPublishesEachFile(t *testing.T) {
	var calls []string
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		calls = append(calls, r.Method+" "+apiPath(r)+" "+body["visibility"].(string))
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"x","visibility":"link","public_url":"https://app.test/f/t/x.pdf"}`)
	})
	t.Setenv("RUNTM_SESSION_ID", "")
	t.Setenv("RUNTM_ORG_ID", "org-1")

	cmd := newAssetsPublish(rt)
	cmd.SetArgs([]string{"a-1", "a-2"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("publish: %v", err)
	}

	if strings.Join(calls, ",") != "PATCH /assets/a-1 link,PATCH /assets/a-2 link" {
		t.Errorf("calls = %v", calls)
	}
	var out struct {
		Status string           `json:"status"`
		Assets []map[string]any `json:"assets"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil || out.Status != "public" || len(out.Assets) != 2 {
		t.Errorf("stdout = %s", stdout.String())
	}
}

func TestAssetsPublishOutsideASessionSaysWhatWentPublicBeforeAFailure(t *testing.T) {
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/assets/a-2") {
			w.WriteHeader(http.StatusNotFound)
			_, _ = io.WriteString(w, `{"detail":{"code":"ASSET_NOT_FOUND"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"id":"a-1","visibility":"link"}`)
	})
	t.Setenv("RUNTM_SESSION_ID", "")
	t.Setenv("RUNTM_ORG_ID", "org-1")

	cmd := newAssetsPublish(rt)
	cmd.SetArgs([]string{"a-1", "a-2", "a-3"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("want the 404 for a-2")
	}

	var out struct {
		Status string           `json:"status"`
		Assets []map[string]any `json:"assets"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil || out.Status != "partial" || len(out.Assets) != 1 || out.Assets[0]["id"] != "a-1" {
		t.Errorf("stdout = %s, want a-1 reported as published", stdout.String())
	}
}

func TestAssetsListAndGetPassTheirFilters(t *testing.T) {
	var got []string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		got = append(got, apiPath(r)+"?"+r.URL.RawQuery)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"assets":[]}`)
	})
	t.Setenv("RUNTM_SESSION_ID", "ses-1")
	t.Setenv("RUNTM_ORG_ID", "org-1")

	for _, args := range [][]string{
		{"list", "--visibility", "link", "--limit", "5"},
		{"list", "--all"},
		{"get", "a-1", "--attachment"},
	} {
		cmd := NewAssetsCommand(rt)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("%v: %v", args, err)
		}
	}

	want := []string{
		"/v0/sessions/ses-1/assets?limit=5&visibility=link",
		"/assets?",
		"/v0/sessions/ses-1/assets/a-1?disposition=attachment",
	}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Errorf("requests = %v, want %v", got, want)
	}
}

func TestAssetsListRejectsBadFlags(t *testing.T) {
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
	})
	for _, args := range [][]string{
		{"--all", "--session", "ses-1"},
		{"--visibility", "public"},
	} {
		cmd := newAssetsList(rt)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil {
			t.Errorf("%v: want an error", args)
		}
	}
}

func TestAssetsUnpublishAndDelete(t *testing.T) {
	var calls []string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		calls = append(calls, r.Method+" "+apiPath(r)+" "+string(body))
		if r.Method == http.MethodDelete {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"a-1","visibility":"private"}`)
	})
	t.Setenv("RUNTM_ORG_ID", "org-1")

	unpublish := newAssetsUnpublish(rt)
	unpublish.SetArgs([]string{"a-1"})
	if err := unpublish.Execute(); err != nil {
		t.Fatalf("unpublish: %v", err)
	}
	unconfirmed := newAssetsDelete(rt)
	unconfirmed.SetArgs([]string{"a-1"})
	if err := unconfirmed.Execute(); !errors.Is(err, errSilent) {
		t.Fatalf("delete without --yes: err = %v, want errSilent", err)
	}
	confirmed := newAssetsDelete(rt)
	confirmed.SetArgs([]string{"a-1", "--yes"})
	if err := confirmed.Execute(); err != nil {
		t.Fatalf("delete: %v", err)
	}

	want := []string{`PATCH /assets/a-1 {"visibility":"private"}`, "DELETE /assets/a-1 "}
	if strings.Join(calls, "\n") != strings.Join(want, "\n") {
		t.Errorf("calls = %q, want %q", calls, want)
	}
}
