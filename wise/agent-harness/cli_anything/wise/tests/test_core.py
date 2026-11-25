"""Unit tests for cli-anything-wise — no network, no Wise account needed.

A fake ``requests.Session`` records every call and returns canned responses,
so we can assert both request shapes (paths, params, bodies, headers) and
CLI behaviour (JSON output, confirmations, error codes).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
import requests
from click.testing import CliRunner

from cli_anything.wise import wise_cli
from cli_anything.wise.core import api
from cli_anything.wise.core import config as cfg
from cli_anything.wise.core.client import WiseAPIError, WiseClient, sign_one_time_token


# ── fakes ─────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, headers: dict | None = None,
                 content: bytes | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.reason = "OK" if status < 400 else "Error"
        if content is not None:
            self.content = content
        elif body is None:
            self.content = b""
        else:
            self.content = json.dumps(body).encode()
        self.text = self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        if not self.content:
            raise ValueError("no body")
        return json.loads(self.content)


class FakeSession:
    """Routes (METHOD, path-without-base) → response or list of responses."""

    def __init__(self, routes: dict[tuple[str, str], Any]):
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        path = url.split(".com", 1)[1] if ".com" in url else url
        self.calls.append({"method": method, "path": path, "params": params,
                           "body": json.loads(data) if data else None, "headers": headers})
        resp = self.routes.get((method, path))
        if resp is None:
            return FakeResponse(404, {"errors": [{"code": "NOT_FOUND", "message": f"no route {path}"}]})
        if isinstance(resp, list):
            return resp.pop(0)
        return resp


def make_client(routes, **settings_kw) -> tuple[WiseClient, FakeSession]:
    sess = FakeSession(routes)
    settings = cfg.Settings(token="test-token-123456", **settings_kw)
    return WiseClient(settings, session=sess), sess


PROFILES = [
    {"id": 111, "type": "BUSINESS", "businessName": "ACME Srl"},
    {"id": 222, "type": "PERSONAL", "fullName": "Mario Rossi"},
]
QUOTE = {
    "id": "q-uuid-1", "sourceCurrency": "EUR", "targetCurrency": "GBP", "rate": 0.85,
    "sourceAmount": 100, "expirationTime": "2026-11-25T20:00:00Z",
    "paymentOptions": [
        {"payIn": "BANK_TRANSFER", "sourceAmount": 100, "targetAmount": 84.2,
         "fee": {"total": 0.9}, "disabled": False},
        {"payIn": "BALANCE", "sourceAmount": 100, "targetAmount": 84.4,
         "fee": {"total": 0.7}, "disabled": False, "formattedEstimatedDelivery": "by Friday"},
    ],
}


# ── config ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WISE_CONFIG_FILE", str(tmp_path / "config.json"))
    for var in ("WISE_API_TOKEN", "WISE_ENV", "WISE_PROFILE_ID", "WISE_PRIVATE_KEY_PATH", "WISE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


class TestConfig:
    def test_default_is_sandbox(self):
        s = cfg.resolve()
        assert s.env == "sandbox"
        assert s.base_url == "https://api.wise-sandbox.com"
        assert s.token is None

    def test_precedence_cli_over_env_over_file(self, monkeypatch):
        cfg.save_file({"token": "file-token", "env": "production", "profile_id": 1})
        assert cfg.resolve().token == "file-token"
        assert cfg.resolve().env == "production"
        monkeypatch.setenv("WISE_API_TOKEN", "env-token")
        monkeypatch.setenv("WISE_PROFILE_ID", "7")
        s = cfg.resolve()
        assert s.token == "env-token" and s.profile_id == 7
        s = cfg.resolve(token="cli-token", env="sandbox", profile_id=9)
        assert (s.token, s.env, s.profile_id) == ("cli-token", "sandbox", 9)

    def test_config_file_is_private(self):
        path = cfg.save_file({"token": "secret", "junk": "dropped"})
        assert oct(path.stat().st_mode & 0o777) == "0o600"
        assert json.loads(path.read_text()) == {"token": "secret"}

    def test_unknown_env_rejected(self):
        with pytest.raises(ValueError):
            cfg.Settings(env="staging")

    def test_mask(self):
        assert cfg.mask(None) == "(not set)"
        assert cfg.mask("abcdefghijkl") == "abcd…ijkl"
        assert "secret" not in cfg.mask("short")

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("WISE_BASE_URL", "http://localhost:9999")
        assert cfg.resolve().base_url == "http://localhost:9999"


# ── client ────────────────────────────────────────────────────────────


class TestClient:
    def test_auth_header_and_user_agent(self):
        c, sess = make_client({("GET", "/v1/me"): FakeResponse(200, {"id": 1})})
        assert api.get_me(c) == {"id": 1}
        h = sess.calls[0]["headers"]
        assert h["Authorization"] == "Bearer test-token-123456"
        assert h["User-Agent"].startswith("cli-anything-wise/")

    def test_missing_token(self):
        c = WiseClient(cfg.Settings(token=None), session=FakeSession({}))
        with pytest.raises(WiseAPIError) as e:
            api.get_me(c)
        assert e.value.status == 401

    def test_error_message_extraction(self):
        c, _ = make_client({("POST", "/v1/transfers"): FakeResponse(
            422, {"errors": [{"code": "X", "message": "bad iban", "path": "iban"}]})})
        with pytest.raises(WiseAPIError) as e:
            api.create_transfer(c, {})
        assert e.value.status == 422
        assert "iban: bad iban" in e.value.message

    def test_network_error_wrapped(self):
        class Boom:
            def request(self, *a, **k):
                raise requests.ConnectionError("down")
        c = WiseClient(cfg.Settings(token="t"), session=Boom())
        with pytest.raises(WiseAPIError) as e:
            api.get_me(c)
        assert e.value.status == 0 and "Network error" in e.value.message

    def test_none_params_dropped(self):
        c, sess = make_client({("GET", "/v1/rates"): FakeResponse(200, [])})
        api.get_rates(c, "eur", None)
        assert sess.calls[0]["params"] == {"source": "EUR"}

    def test_sca_without_key_explains(self):
        challenge = FakeResponse(403, {}, {"x-2fa-approval-result": "REJECTED", "x-2fa-approval": "ott-1"})
        c, _ = make_client({("POST", "/v3/profiles/1/transfers/5/payments"): challenge})
        with pytest.raises(WiseAPIError) as e:
            api.fund_transfer(c, 1, 5)
        assert "Strong Customer Authentication" in e.value.message

    def test_sca_signs_and_retries(self, tmp_path):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "private.pem"
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        challenge = FakeResponse(403, {}, {"x-2fa-approval-result": "REJECTED", "x-2fa-approval": "ott-xyz"})
        ok = FakeResponse(201, {"status": "COMPLETED"})
        c, sess = make_client({("POST", "/v3/profiles/1/transfers/5/payments"): [challenge, ok]},
                              private_key_path=str(key_path))
        assert api.fund_transfer(c, 1, 5) == {"status": "COMPLETED"}
        retry = sess.calls[1]["headers"]
        assert retry["x-2fa-approval"] == "ott-xyz"
        sig = base64.b64decode(retry["X-Signature"])
        key.public_key().verify(sig, b"ott-xyz", padding.PKCS1v15(), hashes.SHA256())  # raises if bad
        assert sign_one_time_token("ott-xyz", str(key_path)) == retry["X-Signature"]


# ── api helpers ───────────────────────────────────────────────────────


class TestApiHelpers:
    def test_default_profile_prefers_personal(self):
        c, _ = make_client({("GET", "/v2/profiles"): FakeResponse(200, PROFILES)})
        assert api.default_profile_id(c) == 222
        assert api.default_profile_id(c, "BUSINESS") == 111

    def test_default_profile_none(self):
        c, _ = make_client({("GET", "/v2/profiles"): FakeResponse(200, [])})
        with pytest.raises(WiseAPIError):
            api.default_profile_id(c)

    def test_quote_body_exactly_one_amount(self):
        with pytest.raises(ValueError):
            api.build_quote_body("EUR", "GBP")
        with pytest.raises(ValueError):
            api.build_quote_body("EUR", "GBP", 1, 2)
        b = api.build_quote_body("eur", "gbp", target_amount=50, target_account=9, pay_out="BALANCE")
        assert b == {"sourceCurrency": "EUR", "targetCurrency": "GBP", "targetAmount": 50,
                     "targetAccount": 9, "payOut": "BALANCE"}

    def test_best_payment_option(self):
        assert api.best_payment_option(QUOTE)["payIn"] == "BALANCE"
        assert api.best_payment_option({"paymentOptions": []}) is None
        q = {"paymentOptions": [{"payIn": "BALANCE", "disabled": True}, {"payIn": "CARD"}]}
        assert api.best_payment_option(q)["payIn"] == "CARD"

    def test_iban_recipient_normalized(self):
        b = api.build_iban_recipient(1, "Anna", "eur", "it60 x054 2811 1010 0000 0123 456")
        assert b["details"]["iban"] == "IT60X0542811101000000123456"
        assert b["details"]["legalType"] == "PRIVATE"
        assert b["type"] == "iban" and b["currency"] == "EUR"

    def test_transfer_body_has_idempotency_id(self):
        b1 = api.build_transfer_body(9, "q", "Rent")
        b2 = api.build_transfer_body(9, "q", "Rent")
        assert b1["customerTransactionId"] != b2["customerTransactionId"]
        assert b1["details"] == {"reference": "Rent"}

    def test_statement_dates_normalized(self):
        c, sess = make_client({("GET", "/v1/profiles/1/balance-statements/2/statement.json"):
                               FakeResponse(200, {"transactions": []})})
        api.balance_statement(c, 1, 2, "eur", start="2026-01-01", end="2026-01-31")
        p = sess.calls[0]["params"]
        assert p["intervalStart"] == "2026-01-01T00:00:00.000Z"
        assert p["intervalEnd"] == "2026-01-31T23:59:59.999Z"
        assert p["currency"] == "EUR"

    def test_recipients_unwraps_content(self):
        c, _ = make_client({("GET", "/v2/accounts"): FakeResponse(200, {"content": [{"id": 1}]})})
        assert api.list_recipients(c, 5) == [{"id": 1}]


# ── CLI ───────────────────────────────────────────────────────────────


@pytest.fixture
def fake(monkeypatch):
    """Install a FakeSession into every WiseClient the CLI builds."""
    holder: dict[str, FakeSession] = {}

    def install(routes):
        sess = FakeSession(routes)
        holder["s"] = sess
        orig_init = WiseClient.__init__

        def init(self, settings, session=None, timeout=30):
            orig_init(self, settings, session=sess, timeout=timeout)
        monkeypatch.setattr(WiseClient, "__init__", init)
        return sess
    return install


def run(*args, input=None):
    return CliRunner().invoke(wise_cli.cli, ["--token", "tok-abcdefgh", *args], input=input)


class TestCLI:
    def test_help_lists_groups(self):
        r = CliRunner().invoke(wise_cli.cli, ["--help"])
        assert r.exit_code == 0
        for g in ("balances", "transfers", "recipients", "quotes", "rates"):
            assert g in r.output

    def test_config_set_and_show_masks_token(self):
        r = CliRunner().invoke(wise_cli.cli, ["config", "set", "--token", "supersecrettoken", "--profile", "5"])
        assert r.exit_code == 0, r.output
        r = CliRunner().invoke(wise_cli.cli, ["--json", "config", "show"])
        data = json.loads(r.output)
        assert data["profile_id"] == 5
        assert "supersecrettoken" not in r.output

    def test_no_token_json_error(self):
        r = CliRunner().invoke(wise_cli.cli, ["--json", "whoami"])
        assert r.exit_code == 1
        assert json.loads(r.stderr if hasattr(r, "stderr") and r.stderr else r.output)["ok"] is False

    def test_balances_list_uses_default_profile(self, fake):
        sess = fake({
            ("GET", "/v2/profiles"): FakeResponse(200, PROFILES),
            ("GET", "/v4/profiles/222/balances"): FakeResponse(200, [
                {"id": 9, "currency": "EUR", "type": "STANDARD",
                 "amount": {"value": 1234.5, "currency": "EUR"},
                 "reservedAmount": {"value": 0, "currency": "EUR"}}]),
        })
        r = run("balances", "list")
        assert r.exit_code == 0, r.output
        assert "1,234.50 EUR" in r.output
        assert sess.calls[1]["params"] == {"types": "STANDARD"}

    def test_rates_json(self, fake):
        fake({("GET", "/v1/rates"): FakeResponse(200, [{"source": "EUR", "target": "USD", "rate": 1.08}])})
        r = run("--json", "rates", "eur", "usd")
        assert r.exit_code == 0
        assert json.loads(r.output)[0]["rate"] == 1.08

    def test_quote_human(self, fake):
        fake({("POST", "/v3/profiles/7/quotes"): FakeResponse(200, QUOTE)})
        r = run("--profile", "7", "quotes", "create", "100", "EUR", "GBP")
        assert r.exit_code == 0, r.output
        assert "84.40 GBP" in r.output and "by Friday" in r.output

    def test_send_dry_run_creates_nothing(self, fake):
        sess = fake({("POST", "/v3/profiles/7/quotes"): FakeResponse(200, QUOTE)})
        r = run("--json", "--profile", "7", "transfers", "send", "100", "EUR", "GBP",
                "--to", "55", "--reference", "Rent", "--dry-run")
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["dry_run"] is True
        assert out["transfer_request"]["targetAccount"] == 55
        assert [c["path"] for c in sess.calls] == ["/v3/profiles/7/quotes"]
        assert sess.calls[0]["body"]["targetAccount"] == 55

    def test_send_json_without_yes_refuses(self, fake):
        sess = fake({("POST", "/v3/profiles/7/quotes"): FakeResponse(200, QUOTE)})
        r = run("--json", "--profile", "7", "transfers", "send", "100", "EUR", "GBP", "--to", "55")
        assert r.exit_code == 1
        assert all(c["path"] != "/v1/transfers" for c in sess.calls)

    def test_send_interactive_decline(self, fake, monkeypatch):
        monkeypatch.setattr(wise_cli.sys.stdin, "isatty", lambda: True, raising=False)
        sess = fake({("POST", "/v3/profiles/7/quotes"): FakeResponse(200, QUOTE)})
        r = run("--profile", "7", "transfers", "send", "100", "EUR", "GBP", "--to", "55", input="n\n")
        assert r.exit_code != 0
        assert all(c["path"] != "/v1/transfers" for c in sess.calls)

    def test_send_and_fund_with_yes(self, fake):
        sess = fake({
            ("POST", "/v3/profiles/7/quotes"): FakeResponse(200, QUOTE),
            ("POST", "/v1/transfers"): FakeResponse(200, {"id": 900, "status": "incoming_payment_waiting"}),
            ("POST", "/v3/profiles/7/transfers/900/payments"): FakeResponse(201, {"status": "COMPLETED"}),
        })
        r = run("--json", "--profile", "7", "transfers", "send", "100", "EUR", "GBP",
                "--to", "55", "--fund", "--yes")
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["transfer"]["id"] == 900 and out["payment"]["status"] == "COMPLETED"
        tbody = sess.calls[1]["body"]
        assert tbody["quoteUuid"] == "q-uuid-1" and tbody["targetAccount"] == 55
        assert sess.calls[2]["body"] == {"type": "BALANCE"}

    def test_cancel(self, fake):
        sess = fake({("PUT", "/v1/transfers/900/cancel"): FakeResponse(200, {"id": 900, "status": "cancelled"})})
        r = run("transfers", "cancel", "900", "--yes")
        assert r.exit_code == 0 and "cancelled" in r.output
        assert sess.calls[0]["method"] == "PUT"

    def test_recipient_add_iban(self, fake):
        sess = fake({("POST", "/v1/accounts"): FakeResponse(200, {"id": 31})})
        r = run("--profile", "7", "recipients", "add-iban", "--name", "Anna Bianchi",
                "--iban", "IT60X0542811101000000123456")
        assert r.exit_code == 0 and "31" in r.output
        assert sess.calls[0]["body"]["profile"] == 7

    def test_api_error_exit_code(self, fake):
        fake({("GET", "/v1/transfers/1"): FakeResponse(404, {"errors": [{"message": "Not found"}]})})
        r = run("transfers", "get", "1")
        assert r.exit_code == 1

    def test_receipt_written(self, fake, tmp_path):
        fake({("GET", "/v1/transfers/5/receipt.pdf"): FakeResponse(200, content=b"%PDF-1.4 fake")})
        target = tmp_path / "r.pdf"
        r = run("transfers", "receipt", "5", "-o", str(target))
        assert r.exit_code == 0
        assert target.read_bytes().startswith(b"%PDF")

    def test_activity_strips_html(self, fake):
        fake({("GET", "/v1/profiles/7/activities"): FakeResponse(200, {
            "activities": [{"createdOn": "2026-11-20T10:00:00Z", "type": "CARD_PAYMENT", "status": "COMPLETED",
                            "title": "<strong>Coffee Bar</strong>", "primaryAmount": "<negative>3.50 EUR</negative>"}],
            "cursor": "abc"})})
        r = run("--profile", "7", "activity")
        assert r.exit_code == 0
        assert "Coffee Bar" in r.output and "<strong>" not in r.output and "--cursor abc" in r.output

    def test_repl_runs_commands(self, fake):
        fake({("GET", "/v1/rates"): FakeResponse(200, [{"source": "EUR", "target": "USD", "rate": 1.1}])})
        r = CliRunner().invoke(wise_cli.cli, ["--token", "tok-abcdefgh"], input="rates EUR USD\nexit\n")
        assert r.exit_code == 0, r.output
        assert "1.1" in r.output
