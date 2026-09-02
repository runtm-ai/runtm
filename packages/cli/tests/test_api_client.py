"""Tests for APIClient error handling (_handle_error)."""

import httpx
import pytest

from runtm_cli.api_client import APIClient
from runtm_shared.errors import (
    DeploymentNotFoundError,
    InvalidTokenError,
    RateLimitError,
    RuntmError,
)


def _client() -> APIClient:
    # Pass explicit values so construction never touches config/keyring.
    return APIClient(api_url="http://testserver", token="test-token")


def _response(
    status_code: int,
    *,
    headers: dict | None = None,
    json_body=None,
    text: str | None = None,
    url: str = "http://testserver/v1/deployments",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_body is not None:
        return httpx.Response(status_code, headers=headers or {}, json=json_body, request=request)
    return httpx.Response(status_code, headers=headers or {}, text=text or "", request=request)


class TestRetryAfterHeader:
    """Retry-After handling on 429 responses (must not crash)."""

    def test_integer_seconds_parsed(self) -> None:
        """The delta-seconds form should be parsed to an int."""
        with pytest.raises(RateLimitError) as exc_info:
            _client()._handle_error(_response(429, headers={"Retry-After": "120"}))
        assert exc_info.value.retry_after_seconds == 120

    def test_http_date_does_not_crash(self) -> None:
        """The HTTP-date form (RFC 9110) must not raise ValueError."""
        # Old behavior: int("Wed, 21 Oct 2015 07:28:00 GMT") -> ValueError leaks
        # out of _handle_error instead of a clean RateLimitError.
        with pytest.raises(RateLimitError) as exc_info:
            _client()._handle_error(
                _response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
            )
        assert exc_info.value.retry_after_seconds is None

    def test_missing_header(self) -> None:
        """No Retry-After header should yield retry_after_seconds=None."""
        with pytest.raises(RateLimitError) as exc_info:
            _client()._handle_error(_response(429))
        assert exc_info.value.retry_after_seconds is None

    def test_whitespace_and_non_numeric(self) -> None:
        """Padded or garbage values should not crash; they fall back to None."""
        with pytest.raises(RateLimitError) as exc_info:
            _client()._handle_error(_response(429, headers={"Retry-After": "  not-a-number "}))
        assert exc_info.value.retry_after_seconds is None


class TestNonJsonErrorBody:
    """Errors whose body is not JSON should surface the raw server text."""

    def test_text_body_is_surfaced(self) -> None:
        """The server's text body must reach the user, not be swallowed."""
        # Old behavior: the detailed RuntmError was raised inside a
        # `try/except Exception: pass`, so it was swallowed and the generic
        # fallback message always won.
        with pytest.raises(RuntmError) as exc_info:
            _client()._handle_error(_response(500, text="upstream database exploded"))
        assert "upstream database exploded" in (exc_info.value.recovery_hint or "")

    def test_empty_body_uses_generic_message(self) -> None:
        """With no body, the generic recovery hint is used."""
        with pytest.raises(RuntmError) as exc_info:
            _client()._handle_error(_response(500, text=""))
        hint = exc_info.value.recovery_hint or ""
        assert "Check your request format" in hint

    def test_body_is_truncated(self) -> None:
        """A very long body should be truncated to keep the message readable."""
        with pytest.raises(RuntmError) as exc_info:
            _client()._handle_error(_response(500, text="E" * 5000))
        hint = exc_info.value.recovery_hint or ""
        # 200-char cap plus the "Server response: " prefix.
        assert len(hint) < 300


class TestStructuredErrors:
    """The existing JSON/status-based error mapping must still work."""

    def test_json_error_message_and_hint(self) -> None:
        """A structured JSON error should map to message + recovery hint."""
        with pytest.raises(RuntmError) as exc_info:
            _client()._handle_error(
                _response(
                    400,
                    json_body={"error": "bad request thing", "recovery_hint": "do the X"},
                )
            )
        assert exc_info.value.message == "bad request thing"
        assert exc_info.value.recovery_hint == "do the X"

    def test_401_is_invalid_token(self) -> None:
        with pytest.raises(InvalidTokenError):
            _client()._handle_error(_response(401))

    def test_404_deployment_not_found(self) -> None:
        with pytest.raises(DeploymentNotFoundError):
            _client()._handle_error(
                _response(404, url="http://testserver/v1/deployments/dep_abc123")
            )
