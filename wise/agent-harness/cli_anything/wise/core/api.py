"""High-level wrappers around Wise Platform API endpoints.

Each function takes a :class:`~cli_anything.wise.core.client.WiseClient` and
returns the decoded JSON payload. Endpoint references:
https://docs.wise.com/api-docs/api-reference
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .client import WiseAPIError, WiseClient

# ── Profiles & user ───────────────────────────────────────────────────


def get_me(c: WiseClient) -> dict:
    return c.get("/v1/me")


def list_profiles(c: WiseClient) -> list[dict]:
    return c.get("/v2/profiles")


def get_profile(c: WiseClient, profile_id: int) -> dict:
    return c.get(f"/v2/profiles/{profile_id}")


def default_profile_id(c: WiseClient, kind: str = "PERSONAL") -> int:
    """Return the first profile of the given type (PERSONAL/BUSINESS)."""
    profiles = list_profiles(c) or []
    for p in profiles:
        if str(p.get("type", "")).upper() == kind.upper():
            return int(p["id"])
    if profiles:
        return int(profiles[0]["id"])
    raise WiseAPIError(404, "No Wise profiles found for this token.")


# ── Balances ──────────────────────────────────────────────────────────


def list_balances(c: WiseClient, profile_id: int, types: str = "STANDARD") -> list[dict]:
    return c.get(f"/v4/profiles/{profile_id}/balances", params={"types": types})


def get_balance(c: WiseClient, profile_id: int, balance_id: int) -> dict:
    return c.get(f"/v4/profiles/{profile_id}/balances/{balance_id}")


def open_balance(c: WiseClient, profile_id: int, currency: str,
                 jar_name: str | None = None) -> dict:
    body: dict[str, Any] = {"currency": currency.upper(), "type": "STANDARD"}
    if jar_name:
        body = {"currency": currency.upper(), "type": "SAVINGS", "name": jar_name}
    return c.post(
        f"/v4/profiles/{profile_id}/balances",
        body,
        headers={"X-idempotence-uuid": str(uuid.uuid4())},
    )


def close_balance(c: WiseClient, profile_id: int, balance_id: int) -> dict:
    return c.delete(f"/v4/profiles/{profile_id}/balances/{balance_id}")


def convert_balance(c: WiseClient, profile_id: int, quote_id: str) -> dict:
    """Move money between your own balances using a quote with payOut=BALANCE."""
    return c.post(
        f"/v2/profiles/{profile_id}/balance-movements",
        {"quoteId": quote_id},
        headers={"X-idempotence-uuid": str(uuid.uuid4())},
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def balance_statement(
    c: WiseClient,
    profile_id: int,
    balance_id: int,
    currency: str,
    start: str | None = None,
    end: str | None = None,
    days: int = 30,
    statement_type: str = "COMPACT",
    fmt: str = "json",
) -> Any:
    now = datetime.now(timezone.utc)
    interval_end = end or _iso(now)
    interval_start = start or _iso(now - timedelta(days=days))
    return c.get(
        f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.{fmt}",
        params={
            "currency": currency.upper(),
            "intervalStart": _normalize_date(interval_start),
            "intervalEnd": _normalize_date(interval_end, end_of_day=True),
            "type": statement_type,
        },
        raw=fmt != "json",
    )


def _normalize_date(value: str, end_of_day: bool = False) -> str:
    """Accept YYYY-MM-DD or a full ISO timestamp."""
    if len(value) == 10:
        return f"{value}T23:59:59.999Z" if end_of_day else f"{value}T00:00:00.000Z"
    return value


# ── Rates & currencies ────────────────────────────────────────────────


def get_rates(c: WiseClient, source: str | None = None, target: str | None = None,
              time: str | None = None) -> list[dict]:
    return c.get(
        "/v1/rates",
        params={
            "source": source.upper() if source else None,
            "target": target.upper() if target else None,
            "time": time,
        },
    )


def list_currencies(c: WiseClient) -> list[dict]:
    return c.get("/v1/currencies")


# ── Quotes ────────────────────────────────────────────────────────────


def build_quote_body(
    source: str,
    target: str,
    source_amount: float | None = None,
    target_amount: float | None = None,
    target_account: int | None = None,
    pay_out: str | None = None,
) -> dict:
    if (source_amount is None) == (target_amount is None):
        raise ValueError("Provide exactly one of source amount or target amount.")
    body: dict[str, Any] = {
        "sourceCurrency": source.upper(),
        "targetCurrency": target.upper(),
    }
    if source_amount is not None:
        body["sourceAmount"] = source_amount
    else:
        body["targetAmount"] = target_amount
    if target_account is not None:
        body["targetAccount"] = target_account
    if pay_out:
        body["payOut"] = pay_out
    return body


def create_quote(c: WiseClient, profile_id: int, body: dict) -> dict:
    return c.post(f"/v3/profiles/{profile_id}/quotes", body)


def get_quote(c: WiseClient, profile_id: int, quote_id: str) -> dict:
    return c.get(f"/v3/profiles/{profile_id}/quotes/{quote_id}")


def update_quote(c: WiseClient, profile_id: int, quote_id: str, target_account: int) -> dict:
    return c.request(
        "PATCH",
        f"/v3/profiles/{profile_id}/quotes/{quote_id}",
        json_body={"targetAccount": target_account},
        headers={"Content-Type": "application/merge-patch+json"},
    )


def best_payment_option(quote: dict, pay_in: str = "BALANCE") -> dict | None:
    """Pick the enabled payment option matching pay_in (defaults to BALANCE)."""
    options = [o for o in quote.get("paymentOptions", []) if not o.get("disabled")]
    for o in options:
        if o.get("payIn") == pay_in:
            return o
    return options[0] if options else None


# ── Recipients (accounts) ─────────────────────────────────────────────


def list_recipients(c: WiseClient, profile_id: int, currency: str | None = None) -> Any:
    data = c.get(
        "/v2/accounts",
        params={"profileId": profile_id, "currency": currency.upper() if currency else None},
    )
    if isinstance(data, dict) and "content" in data:
        return data["content"]
    return data


def get_recipient(c: WiseClient, account_id: int) -> dict:
    return c.get(f"/v2/accounts/{account_id}")


def create_recipient(c: WiseClient, body: dict) -> dict:
    return c.post("/v1/accounts", body)


def delete_recipient(c: WiseClient, account_id: int) -> Any:
    return c.delete(f"/v1/accounts/{account_id}")


def recipient_requirements(c: WiseClient, quote_id: str) -> list[dict]:
    return c.get(f"/v1/quotes/{quote_id}/account-requirements")


def build_iban_recipient(profile_id: int, name: str, currency: str, iban: str,
                         legal_type: str = "PRIVATE", email: str | None = None) -> dict:
    body: dict[str, Any] = {
        "profile": profile_id,
        "accountHolderName": name,
        "currency": currency.upper(),
        "type": "iban",
        "details": {"legalType": legal_type.upper(), "iban": iban.replace(" ", "").upper()},
    }
    if email:
        body["details"]["email"] = email
    return body


def build_email_recipient(profile_id: int, name: str, currency: str, email: str) -> dict:
    return {
        "profile": profile_id,
        "accountHolderName": name,
        "currency": currency.upper(),
        "type": "email",
        "details": {"email": email},
    }


# ── Transfers ─────────────────────────────────────────────────────────


def list_transfers(
    c: WiseClient,
    profile_id: int,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    return c.get(
        "/v1/transfers",
        params={
            "profile": profile_id,
            "limit": limit,
            "offset": offset,
            "status": status,
            "createdDateStart": since,
            "createdDateEnd": until,
        },
    )


def get_transfer(c: WiseClient, transfer_id: int) -> dict:
    return c.get(f"/v1/transfers/{transfer_id}")


def build_transfer_body(target_account: int, quote_uuid: str, reference: str | None = None,
                        customer_transaction_id: str | None = None) -> dict:
    details: dict[str, Any] = {}
    if reference:
        details["reference"] = reference
    return {
        "targetAccount": target_account,
        "quoteUuid": quote_uuid,
        "customerTransactionId": customer_transaction_id or str(uuid.uuid4()),
        "details": details,
    }


def create_transfer(c: WiseClient, body: dict) -> dict:
    return c.post("/v1/transfers", body)


def fund_transfer(c: WiseClient, profile_id: int, transfer_id: int,
                  method: str = "BALANCE") -> dict:
    return c.post(
        f"/v3/profiles/{profile_id}/transfers/{transfer_id}/payments",
        {"type": method},
    )


def cancel_transfer(c: WiseClient, transfer_id: int) -> dict:
    return c.put(f"/v1/transfers/{transfer_id}/cancel")


def delivery_estimate(c: WiseClient, transfer_id: int) -> dict:
    return c.get(f"/v1/delivery-estimates/{transfer_id}")


def transfer_receipt_pdf(c: WiseClient, transfer_id: int) -> bytes:
    return c.get(f"/v1/transfers/{transfer_id}/receipt.pdf", raw=True,
                 headers={"Accept": "application/pdf"})


# ── Activities ────────────────────────────────────────────────────────


def list_activities(c: WiseClient, profile_id: int, size: int = 20,
                    since: str | None = None, until: str | None = None,
                    status: str | None = None, cursor: str | None = None) -> dict:
    return c.get(
        f"/v1/profiles/{profile_id}/activities",
        params={
            "size": size,
            "since": since,
            "until": until,
            "status": status,
            "nextCursor": cursor,
        },
    )
