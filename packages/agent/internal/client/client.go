// Package client is a thin HTTP wrapper for the Runtm Cloud API.
//
// Each public method maps to one endpoint. The client handles auth headers,
// org scoping, JSON marshaling, error decoding and SSE streaming. It returns
// raw JSON bytes so the CLI can pass responses straight to stdout without
// modeling every response schema.
package client

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/runtm-ai/runtm/packages/agent/internal/auth"
)

// Client wraps net/http with Runtm-specific auth + base URL handling.
type Client struct {
	http  *http.Client
	creds *auth.Credentials
}

// New returns a client bound to the supplied credentials. The HTTP timeout is
// long because session prompts can take minutes; individual calls may pass a
// per-request timeout via context if needed in the future.
func New(creds *auth.Credentials) *Client {
	return &Client{
		http: &http.Client{
			Timeout: 0, // No global timeout - SSE streams are long-lived.
		},
		creds: creds,
	}
}

// APIError is returned for any non-2xx response.
type APIError struct {
	Status int
	Body   []byte
	Detail string
}

func (e *APIError) Error() string {
	if e.Detail != "" {
		return fmt.Sprintf("api error %d: %s", e.Status, e.Detail)
	}
	if len(e.Body) > 0 {
		return fmt.Sprintf("api error %d: %s", e.Status, strings.TrimSpace(string(e.Body)))
	}
	return fmt.Sprintf("api error %d", e.Status)
}

// IsAuthError reports whether the error is a 401/403 from the API.
func IsAuthError(err error) bool {
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		return false
	}
	return apiErr.Status == http.StatusUnauthorized || apiErr.Status == http.StatusForbidden
}

// Get performs a GET request and returns the raw JSON body.
func (c *Client) Get(path string, query url.Values) ([]byte, error) {
	return c.do(http.MethodGet, path, query, nil)
}

// PostJSON marshals body as JSON and POSTs it, returning the response body.
func (c *Client) PostJSON(path string, body any) ([]byte, error) {
	payload, err := marshal(body)
	if err != nil {
		return nil, err
	}
	return c.do(http.MethodPost, path, nil, payload)
}

// PutJSON marshals body as JSON and PUTs it, returning the response body.
func (c *Client) PutJSON(path string, body any) ([]byte, error) {
	payload, err := marshal(body)
	if err != nil {
		return nil, err
	}
	return c.do(http.MethodPut, path, nil, payload)
}

// PatchJSON marshals body as JSON and PATCHes it, returning the response body.
func (c *Client) PatchJSON(path string, body any) ([]byte, error) {
	payload, err := marshal(body)
	if err != nil {
		return nil, err
	}
	return c.do(http.MethodPatch, path, nil, payload)
}

// Delete performs a DELETE request and returns the response body.
func (c *Client) Delete(path string) ([]byte, error) {
	return c.do(http.MethodDelete, path, nil, nil)
}

// DeleteJSON performs a DELETE with a JSON body (some routes use this).
func (c *Client) DeleteJSON(path string, body any) ([]byte, error) {
	payload, err := marshal(body)
	if err != nil {
		return nil, err
	}
	return c.do(http.MethodDelete, path, nil, payload)
}

// StreamSSE POSTs body and streams Server-Sent Events to out. Each event is
// written as a single JSON line so the calling agent can consume it line by
// line. Returns when the stream closes or the server emits a `done` event.
func (c *Client) StreamSSE(path string, body any, out io.Writer) error {
	payload, err := marshal(body)
	if err != nil {
		return err
	}
	return c.streamSSE(http.MethodPost, path, payload, out)
}

// StreamSSEGet streams a GET endpoint that returns text/event-stream. Used by
// long-poll endpoints like /build-logs and /events.
func (c *Client) StreamSSEGet(path string, out io.Writer) error {
	return c.streamSSE(http.MethodGet, path, nil, out)
}

func (c *Client) streamSSE(method, path string, body []byte, out io.Writer) error {
	req, err := c.newRequest(method, path, nil, body)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "text/event-stream")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return decodeError(resp)
	}

	enc := json.NewEncoder(out)
	scanner := bufio.NewScanner(resp.Body)
	// SSE events can be large -- bump the buffer to 1 MiB.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	var (
		eventType string
		dataLines []string
	)
	flush := func() error {
		if eventType == "" && len(dataLines) == 0 {
			return nil
		}
		data := strings.Join(dataLines, "\n")
		// If the data is valid JSON, surface its fields directly; otherwise
		// pass the raw string so the consumer still sees something.
		var rawData any
		if err := json.Unmarshal([]byte(data), &rawData); err != nil {
			rawData = data
		}
		envelope := map[string]any{
			"event": eventType,
			"data":  rawData,
		}
		if err := enc.Encode(envelope); err != nil {
			return err
		}
		eventType = ""
		dataLines = dataLines[:0]
		return nil
	}

	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if err := flush(); err != nil {
				return err
			}
			continue
		}
		switch {
		case strings.HasPrefix(line, "event:"):
			eventType = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
		case strings.HasPrefix(line, "data:"):
			dataLines = append(dataLines, strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		case strings.HasPrefix(line, ":"):
			// Comment / keep-alive, ignore.
		}
	}
	if err := flush(); err != nil {
		return err
	}
	if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
		return fmt.Errorf("stream read failed: %w", err)
	}
	return nil
}

func (c *Client) do(method, path string, query url.Values, body []byte) ([]byte, error) {
	req, err := c.newRequest(method, path, query, body)
	if err != nil {
		return nil, err
	}
	// Cap non-streaming requests at 2 minutes; SSE bypasses this path.
	c.http.Timeout = 2 * time.Minute

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read body: %w", err)
	}

	if resp.StatusCode >= 400 {
		return nil, parseAPIError(resp.StatusCode, respBody)
	}
	return respBody, nil
}

func (c *Client) newRequest(method, path string, query url.Values, body []byte) (*http.Request, error) {
	endpoint, err := c.url(path, query)
	if err != nil {
		return nil, err
	}

	var reader io.Reader
	if len(body) > 0 {
		reader = bytes.NewReader(body)
	}

	req, err := http.NewRequest(method, endpoint, reader)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.creds.APIKey)
	req.Header.Set("Accept", "application/json")
	if len(body) > 0 {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.creds.OrganizationID != "" {
		req.Header.Set("X-Organization-Id", c.creds.OrganizationID)
	}
	return req, nil
}

func (c *Client) url(path string, query url.Values) (string, error) {
	base := strings.TrimRight(c.creds.APIURL, "/")
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	full := base + path
	if len(query) > 0 {
		full += "?" + query.Encode()
	}
	if _, err := url.Parse(full); err != nil {
		return "", fmt.Errorf("invalid URL: %w", err)
	}
	return full, nil
}

func decodeError(resp *http.Response) error {
	body, _ := io.ReadAll(resp.Body)
	return parseAPIError(resp.StatusCode, body)
}

// parseAPIError pulls out FastAPI's typical {"detail": "..."} shape but also
// handles nested error objects like {"detail": {"error": "..."}}.
func parseAPIError(status int, body []byte) error {
	apiErr := &APIError{Status: status, Body: body}

	var generic map[string]any
	if err := json.Unmarshal(body, &generic); err == nil {
		switch detail := generic["detail"].(type) {
		case string:
			apiErr.Detail = detail
		case map[string]any:
			if msg, ok := detail["error"].(string); ok {
				apiErr.Detail = msg
			} else if msg, ok := detail["message"].(string); ok {
				apiErr.Detail = msg
			}
		}
		if apiErr.Detail == "" {
			if msg, ok := generic["error"].(string); ok {
				apiErr.Detail = msg
			}
		}
	}
	return apiErr
}

func marshal(body any) ([]byte, error) {
	if body == nil {
		return nil, nil
	}
	out, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal body: %w", err)
	}
	return out, nil
}
