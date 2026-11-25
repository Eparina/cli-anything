"""Thin HTTP client for the Wise Platform API.

Features
--------
* Bearer-token authentication with a personal API token.
* Transparent Strong Customer Authentication (SCA) retry: when Wise answers
  ``403`` with ``x-2fa-approval-result: REJECTED`` and a one-time token in
  ``x-2fa-approval``, the client signs that token with the user's RSA private
  key (SHA256withRSA, base64) and replays the request with the
  ``x-2fa-approval`` and ``X-Signature`` headers – exactly as documented at
  https://docs.wise.com/api-docs/features/strong-customer-authentication-2fa
* Structured :class:`WiseAPIError` carrying status code and Wise error payload.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import requests

from .. import __version__
from .config import Settings

USER_AGENT = f"cli-anything-wise/{__version__}"
DEFAULT_TIMEOUT = 30


class WiseAPIError(Exception):
    """Raised for any non-2xx response or transport problem."""

    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "message": self.message, "details": self.payload}


class WiseAuthError(WiseAPIError):
    """Raised when no token is configured."""


def _extract_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for err in errors:
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("code")
                    path = err.get("path") or err.get("field")
                    parts.append(f"{path}: {msg}" if path else str(msg))
            if parts:
                return "; ".join(parts)
        for key in ("message", "error_description", "error"):
            if payload.get(key):
                return str(payload[key])
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:300]
    return fallback


def sign_one_time_token(one_time_token: str, private_key_path: str) -> str:
    """Sign an SCA one-time token with an RSA private key (PEM)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:  # pragma: no cover - depends on env
        raise WiseAPIError(
            0,
            "SCA signing needs the 'cryptography' package: pip install cryptography",
        ) from exc

    key_bytes = Path(private_key_path).expanduser().read_bytes()
    key = serialization.load_pem_private_key(key_bytes, password=None)
    signature = key.sign(one_time_token.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


class WiseClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.settings = settings
        self.session = session or requests.Session()
        self.timeout = timeout

    # ── low level ─────────────────────────────────────────────────────
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        if not self.settings.token:
            raise WiseAuthError(
                401,
                "No Wise API token configured. Set WISE_API_TOKEN or run "
                "'cli-anything-wise config set --token <TOKEN>'.",
            )
        headers = {
            "Authorization": f"Bearer {self.settings.token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        url = self.settings.base_url.rstrip("/") + path
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        req_headers = self._headers(headers)
        if json_body is not None:
            req_headers["Content-Type"] = "application/json"

        resp = self._send(method, url, clean_params, json_body, req_headers)

        # Strong Customer Authentication challenge → sign & replay once.
        if (
            resp.status_code == 403
            and resp.headers.get("x-2fa-approval-result", "").upper() == "REJECTED"
            and resp.headers.get("x-2fa-approval")
        ):
            ott = resp.headers["x-2fa-approval"]
            if not self.settings.private_key_path:
                raise WiseAPIError(
                    403,
                    "This operation requires Strong Customer Authentication. Upload a "
                    "public key in Wise (Settings → API tokens → Manage public keys) and "
                    "set WISE_PRIVATE_KEY_PATH to the matching private key.",
                    {"one_time_token": ott},
                )
            req_headers = dict(req_headers)
            req_headers["x-2fa-approval"] = ott
            req_headers["X-Signature"] = sign_one_time_token(ott, self.settings.private_key_path)
            resp = self._send(method, url, clean_params, json_body, req_headers)

        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            raise WiseAPIError(
                resp.status_code,
                _extract_message(payload, f"HTTP {resp.status_code} {resp.reason or ''}".strip()),
                payload,
            )

        if raw:
            return resp.content
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _send(self, method, url, params, json_body, headers) -> requests.Response:
        try:
            return self.session.request(
                method,
                url,
                params=params or None,
                data=json.dumps(json_body) if json_body is not None else None,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WiseAPIError(0, f"Network error contacting {url}: {exc}") from exc

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json_body=body if body is not None else {}, **kw)

    def put(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PUT", path, json_body=body, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)
