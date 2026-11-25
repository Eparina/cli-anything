#!/usr/bin/env python3
"""cli-anything-wise — command-line access to your Wise account.

Talks to the official Wise Platform API with a personal API token.
Every command supports ``--json`` for machine-readable output, and running
the binary without a subcommand opens an interactive REPL.

Safety rules:
  * default environment is **sandbox**; pass ``--env production`` (or set
    WISE_ENV=production) to touch real money;
  * every command that moves or cancels money asks for confirmation unless
    ``--yes`` is given, and supports ``--dry-run`` to preview the request.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any, Callable

import click

from . import __version__
from .core import api
from .core import config as cfg
from .core.client import WiseAPIError, WiseClient
from .utils import output as out


class Ctx:
    def __init__(self, json_mode: bool, settings: cfg.Settings):
        self.json = json_mode
        self.settings = settings
        self._client: WiseClient | None = None

    @property
    def client(self) -> WiseClient:
        if self._client is None:
            self._client = WiseClient(self.settings)
        return self._client

    def profile_id(self, explicit: int | None = None) -> int:
        if explicit:
            return explicit
        if self.settings.profile_id:
            return self.settings.profile_id
        pid = api.default_profile_id(self.client)
        self.settings.profile_id = pid
        return pid


pass_ctx = click.make_pass_decorator(Ctx)


def handle_errors(fn: Callable) -> Callable:
    """Turn API/validation errors into clean output + exit code 1."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = click.get_current_context()
        obj = ctx.find_object(Ctx)
        as_json = bool(obj and obj.json)
        try:
            return fn(*args, **kwargs)
        except WiseAPIError as exc:
            out.emit_error(exc.message, as_json, exc.payload, exc.status or None)
            ctx.exit(1)
        except (ValueError, OSError) as exc:
            out.emit_error(str(exc), as_json)
            ctx.exit(1)

    return wrapper


def confirm_or_abort(c: Ctx, yes: bool, message: str) -> None:
    if yes:
        return
    if c.json or not sys.stdin.isatty():
        raise ValueError(
            "Refusing to move money without confirmation in non-interactive mode. "
            "Re-run with --yes."
        )
    env_tag = click.style(c.settings.env.upper(), fg="red" if c.settings.env == "production" else "yellow", bold=True)
    if not click.confirm(f"[{env_tag}] {message}", default=False):
        raise click.Abort()


profile_option = click.option(
    "--profile", "profile_id", type=int, default=None,
    help="Wise profile id (default: configured one, else your PERSONAL profile).",
)


# ── Root ──────────────────────────────────────────────────────────────


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_mode", is_flag=True, help="Machine-readable JSON output.")
@click.option("--token", envvar=None, default=None, help="Wise API token (overrides WISE_API_TOKEN).")
@click.option("--env", "env_name", type=click.Choice(sorted(cfg.ENVIRONMENTS)), default=None,
              help="API environment (default: sandbox).")
@click.option("--profile", "profile_id", type=int, default=None, help="Default profile id.")
@click.version_option(__version__, prog_name="cli-anything-wise")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, token: str | None, env_name: str | None,
        profile_id: int | None) -> None:
    """Wise (TransferWise) CLI: balances, rates, quotes, recipients, transfers."""
    try:
        settings = cfg.resolve(token=token, env=env_name, profile_id=profile_id)
    except ValueError as exc:
        out.emit_error(str(exc), json_mode)
        ctx.exit(1)
    ctx.obj = Ctx(json_mode, settings)
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── config ────────────────────────────────────────────────────────────


@cli.group()
def config() -> None:
    """Show or store credentials and defaults."""


@config.command("show")
@pass_ctx
def config_show(c: Ctx) -> None:
    """Show the effective configuration (token is masked)."""
    view = c.settings.public_view()
    out.emit_json(view) if c.json else out.kv(view, "Wise CLI configuration")


@config.command("set")
@click.option("--token", default=None, help="Personal API token.")
@click.option("--env", "env_name", type=click.Choice(sorted(cfg.ENVIRONMENTS)), default=None)
@click.option("--profile", "profile_id", type=int, default=None)
@click.option("--private-key", "private_key_path", type=click.Path(dir_okay=False), default=None,
              help="PEM private key used to sign SCA challenges.")
@pass_ctx
@handle_errors
def config_set(c: Ctx, token, env_name, profile_id, private_key_path) -> None:
    """Persist settings to the config file (chmod 600)."""
    data = cfg.load_file()
    updates = {"token": token, "env": env_name, "profile_id": profile_id,
               "private_key_path": str(Path(private_key_path).expanduser().resolve()) if private_key_path else None}
    changed = {k: v for k, v in updates.items() if v is not None}
    if not changed:
        raise ValueError("Nothing to set. Pass --token, --env, --profile or --private-key.")
    data.update(changed)
    path = cfg.save_file(data)
    result = {"ok": True, "config_file": str(path), "updated": sorted(changed)}
    out.emit_json(result) if c.json else click.secho(f"✓ Saved {', '.join(sorted(changed))} to {path}", fg="green")


@config.command("clear")
@pass_ctx
def config_clear(c: Ctx) -> None:
    """Delete the config file."""
    path = cfg.config_path()
    existed = path.exists()
    if existed:
        path.unlink()
    out.emit_json({"ok": True, "deleted": existed, "config_file": str(path)}) if c.json \
        else click.echo(f"{'Deleted' if existed else 'No config at'} {path}")


@cli.command("whoami")
@pass_ctx
@handle_errors
def whoami(c: Ctx) -> None:
    """Verify the token and show the current user."""
    me = api.get_me(c.client)
    if c.json:
        out.emit_json(me)
    else:
        out.kv({"id": me.get("id"), "name": me.get("name") or me.get("email"),
                "email": me.get("email"), "active": me.get("active"), "env": c.settings.env},
               "Authenticated as")


# ── profiles ──────────────────────────────────────────────────────────


@cli.group()
def profiles() -> None:
    """Personal and business profiles."""


@profiles.command("list")
@pass_ctx
@handle_errors
def profiles_list(c: Ctx) -> None:
    data = api.list_profiles(c.client)
    if c.json:
        return out.emit_json(data)
    out.table(["ID", "TYPE", "NAME"], [
        [p.get("id"), p.get("type"), p.get("fullName") or p.get("businessName")
         or " ".join(filter(None, [p.get("firstName"), p.get("lastName")]))]
        for p in data
    ])


@profiles.command("get")
@click.argument("profile_id", type=int)
@pass_ctx
@handle_errors
def profiles_get(c: Ctx, profile_id: int) -> None:
    data = api.get_profile(c.client, profile_id)
    out.emit_json(data) if c.json else out.kv(data, f"Profile {profile_id}")


# ── balances ──────────────────────────────────────────────────────────


@cli.group()
def balances() -> None:
    """Multi-currency balances and jars."""


@balances.command("list")
@profile_option
@click.option("--type", "types", default="STANDARD", show_default=True,
              help="STANDARD, SAVINGS or STANDARD,SAVINGS.")
@pass_ctx
@handle_errors
def balances_list(c: Ctx, profile_id, types) -> None:
    """List balances with amounts."""
    data = api.list_balances(c.client, c.profile_id(profile_id), types.upper())
    if c.json:
        return out.emit_json(data)
    out.table(["ID", "CURRENCY", "TYPE", "NAME", "AMOUNT", "RESERVED"], [
        [b.get("id"), b.get("currency"), b.get("type"), b.get("name") or "",
         out.fmt_money(b.get("amount")), out.fmt_money(b.get("reservedAmount"))]
        for b in data
    ])


@balances.command("get")
@click.argument("balance_id", type=int)
@profile_option
@pass_ctx
@handle_errors
def balances_get(c: Ctx, balance_id, profile_id) -> None:
    data = api.get_balance(c.client, c.profile_id(profile_id), balance_id)
    out.emit_json(data) if c.json else out.kv(data, f"Balance {balance_id}")


@balances.command("open")
@click.argument("currency")
@click.option("--jar", "jar_name", default=None, help="Open a savings jar with this name.")
@profile_option
@pass_ctx
@handle_errors
def balances_open(c: Ctx, currency, jar_name, profile_id) -> None:
    """Open a new currency balance (or a savings jar)."""
    data = api.open_balance(c.client, c.profile_id(profile_id), currency, jar_name)
    out.emit_json(data) if c.json else out.kv(data, "Balance opened")


@balances.command("close")
@click.argument("balance_id", type=int)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@profile_option
@pass_ctx
@handle_errors
def balances_close(c: Ctx, balance_id, yes, profile_id) -> None:
    """Close an (empty) balance."""
    confirm_or_abort(c, yes, f"Close balance {balance_id}?")
    data = api.close_balance(c.client, c.profile_id(profile_id), balance_id)
    out.emit_json(data) if c.json else click.secho(f"✓ Balance {balance_id} closed", fg="green")


@balances.command("convert")
@click.argument("amount", type=float)
@click.argument("source")
@click.argument("target")
@click.option("--target-amount", is_flag=True, help="Treat AMOUNT as the amount to receive.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Only quote, do not convert.")
@profile_option
@pass_ctx
@handle_errors
def balances_convert(c: Ctx, amount, source, target, target_amount, yes, dry_run, profile_id) -> None:
    """Convert AMOUNT from SOURCE balance to TARGET balance."""
    pid = c.profile_id(profile_id)
    body = api.build_quote_body(source, target,
                                None if target_amount else amount,
                                amount if target_amount else None,
                                pay_out="BALANCE")
    quote = api.create_quote(c.client, pid, body)
    summary = _quote_summary(quote)
    if dry_run:
        return out.emit_json({"dry_run": True, "quote": quote}) if c.json else out.kv(summary, "Conversion quote (dry run)")
    if not c.json:
        out.kv(summary, "Conversion quote")
    confirm_or_abort(c, yes, f"Convert {summary['you send']} → {summary['recipient gets']}?")
    data = api.convert_balance(c.client, pid, quote["id"])
    out.emit_json({"quote": quote, "conversion": data}) if c.json else click.secho("✓ Conversion completed", fg="green")


@balances.command("statement")
@click.argument("balance_id", type=int)
@click.option("--currency", required=True, help="Currency of the balance, e.g. EUR.")
@click.option("--from", "start", default=None, help="Start date YYYY-MM-DD (default: --days ago).")
@click.option("--to", "end", default=None, help="End date YYYY-MM-DD (default: now).")
@click.option("--days", default=30, show_default=True, type=int)
@click.option("--format", "fmt", type=click.Choice(["json", "csv", "pdf", "xlsx", "camt.053", "mt940"]),
              default="json", show_default=True)
@click.option("--output", "-o", "output_path", type=click.Path(dir_okay=False), default=None,
              help="Write non-JSON formats to this file.")
@profile_option
@pass_ctx
@handle_errors
def balances_statement(c: Ctx, balance_id, currency, start, end, days, fmt, output_path, profile_id) -> None:
    """Download a balance statement (requires SCA in EU/UK)."""
    data = api.balance_statement(c.client, c.profile_id(profile_id), balance_id, currency,
                                 start, end, days, fmt=fmt)
    if fmt != "json":
        path = Path(output_path or f"statement-{balance_id}.{fmt.replace('.', '_')}")
        path.write_bytes(data)
        result = {"ok": True, "file": str(path), "bytes": len(data)}
        return out.emit_json(result) if c.json else click.secho(f"✓ Saved {path} ({len(data)} bytes)", fg="green")
    if c.json:
        return out.emit_json(data)
    txs = data.get("transactions", []) if isinstance(data, dict) else []
    out.table(["DATE", "TYPE", "AMOUNT", "DESCRIPTION", "RUNNING"], [
        [str(t.get("date", ""))[:10], t.get("type"), out.fmt_money(t.get("amount")),
         (t.get("details") or {}).get("description", ""), out.fmt_money(t.get("runningBalance"))]
        for t in txs
    ])


# ── rates / currencies ────────────────────────────────────────────────


@cli.command("rates")
@click.argument("source", required=False)
@click.argument("target", required=False)
@click.option("--time", default=None, help="Historical rate at this ISO timestamp.")
@pass_ctx
@handle_errors
def rates(c: Ctx, source, target, time) -> None:
    """Live mid-market exchange rate (e.g. rates EUR USD)."""
    data = api.get_rates(c.client, source, target, time)
    if c.json:
        return out.emit_json(data)
    out.table(["SOURCE", "TARGET", "RATE", "TIME"],
              [[r.get("source"), r.get("target"), r.get("rate"), r.get("time")] for r in data])


@cli.command("currencies")
@pass_ctx
@handle_errors
def currencies(c: Ctx) -> None:
    """Currencies supported by Wise."""
    data = api.list_currencies(c.client)
    if c.json:
        return out.emit_json(data)
    out.table(["CODE", "SYMBOL", "NAME"], [[x.get("code"), x.get("symbol"), x.get("name")] for x in data])


# ── quotes ────────────────────────────────────────────────────────────


def _quote_summary(q: dict) -> dict:
    opt = api.best_payment_option(q) or {}
    fee = (opt.get("fee") or {}).get("total")
    return {
        "quote id": q.get("id"),
        "rate": q.get("rate"),
        "you send": out.fmt_money(opt.get("sourceAmount", q.get("sourceAmount")), q.get("sourceCurrency")),
        "fee": out.fmt_money(fee, q.get("sourceCurrency")),
        "recipient gets": out.fmt_money(opt.get("targetAmount", q.get("targetAmount")), q.get("targetCurrency")),
        "pay in": opt.get("payIn"),
        "arrives": opt.get("formattedEstimatedDelivery") or opt.get("estimatedDelivery"),
        "expires": q.get("expirationTime"),
    }


@cli.group()
def quotes() -> None:
    """Price a transfer (rate, fee, delivery)."""


@quotes.command("create")
@click.argument("amount", type=float)
@click.argument("source")
@click.argument("target")
@click.option("--target-amount", is_flag=True, help="Treat AMOUNT as the amount the recipient gets.")
@click.option("--recipient", "target_account", type=int, default=None, help="Recipient account id.")
@profile_option
@pass_ctx
@handle_errors
def quotes_create(c: Ctx, amount, source, target, target_amount, target_account, profile_id) -> None:
    """Quote AMOUNT SOURCE → TARGET, e.g. `quotes create 100 EUR GBP`."""
    body = api.build_quote_body(source, target,
                                None if target_amount else amount,
                                amount if target_amount else None,
                                target_account)
    q = api.create_quote(c.client, c.profile_id(profile_id), body)
    out.emit_json(q) if c.json else out.kv(_quote_summary(q), "Quote")


@quotes.command("get")
@click.argument("quote_id")
@profile_option
@pass_ctx
@handle_errors
def quotes_get(c: Ctx, quote_id, profile_id) -> None:
    q = api.get_quote(c.client, c.profile_id(profile_id), quote_id)
    out.emit_json(q) if c.json else out.kv(_quote_summary(q), "Quote")


# ── recipients ────────────────────────────────────────────────────────


@cli.group()
def recipients() -> None:
    """Saved recipient accounts."""


@recipients.command("list")
@click.option("--currency", default=None)
@profile_option
@pass_ctx
@handle_errors
def recipients_list(c: Ctx, currency, profile_id) -> None:
    data = api.list_recipients(c.client, c.profile_id(profile_id), currency)
    if c.json:
        return out.emit_json(data)

    def name(r: dict) -> str:
        n = r.get("name")
        if isinstance(n, dict):
            return n.get("fullName") or ""
        return r.get("accountHolderName") or n or ""

    def summary(r: dict) -> str:
        details = r.get("details") or {}
        return r.get("accountSummary") or details.get("iban") or details.get("email") or ""

    out.table(["ID", "CURRENCY", "TYPE", "NAME", "ACCOUNT"],
              [[r.get("id"), r.get("currency"), r.get("type"), name(r), summary(r)] for r in data])


@recipients.command("get")
@click.argument("account_id", type=int)
@pass_ctx
@handle_errors
def recipients_get(c: Ctx, account_id) -> None:
    data = api.get_recipient(c.client, account_id)
    out.emit_json(data) if c.json else out.kv(data, f"Recipient {account_id}")


@recipients.command("add-iban")
@click.option("--name", required=True, help="Account holder full name.")
@click.option("--iban", required=True)
@click.option("--currency", default="EUR", show_default=True)
@click.option("--business", is_flag=True, help="Recipient is a business.")
@click.option("--email", default=None)
@profile_option
@pass_ctx
@handle_errors
def recipients_add_iban(c: Ctx, name, iban, currency, business, email, profile_id) -> None:
    """Add a bank recipient by IBAN (EUR, CHF, …)."""
    body = api.build_iban_recipient(c.profile_id(profile_id), name, currency, iban,
                                    "BUSINESS" if business else "PRIVATE", email)
    data = api.create_recipient(c.client, body)
    out.emit_json(data) if c.json else click.secho(f"✓ Recipient created: id {data.get('id')}", fg="green")


@recipients.command("add-email")
@click.option("--name", required=True)
@click.option("--email", required=True)
@click.option("--currency", required=True)
@profile_option
@pass_ctx
@handle_errors
def recipients_add_email(c: Ctx, name, email, currency, profile_id) -> None:
    """Add a recipient that Wise contacts by e-mail for bank details."""
    body = api.build_email_recipient(c.profile_id(profile_id), name, currency, email)
    data = api.create_recipient(c.client, body)
    out.emit_json(data) if c.json else click.secho(f"✓ Recipient created: id {data.get('id')}", fg="green")


@recipients.command("add-raw")
@click.argument("json_file", type=click.File("r"))
@pass_ctx
@handle_errors
def recipients_add_raw(c: Ctx, json_file) -> None:
    """Create a recipient from a JSON body (any type, see `recipients requirements`)."""
    import json as _json
    body = _json.load(json_file)
    data = api.create_recipient(c.client, body)
    out.emit_json(data) if c.json else click.secho(f"✓ Recipient created: id {data.get('id')}", fg="green")


@recipients.command("requirements")
@click.argument("quote_id")
@pass_ctx
@handle_errors
def recipients_requirements(c: Ctx, quote_id) -> None:
    """Which recipient fields are required for a quote's route."""
    data = api.recipient_requirements(c.client, quote_id)
    if c.json:
        return out.emit_json(data)
    for req in data:
        click.secho(f"type: {req.get('type')}  ({req.get('title')})", bold=True)
        for f in req.get("fields", []):
            for g in f.get("group", []):
                flag = "*" if g.get("required") else " "
                click.echo(f"  {flag} {g.get('key'):<28} {g.get('name')}")


@recipients.command("delete")
@click.argument("account_id", type=int)
@click.option("--yes", is_flag=True)
@pass_ctx
@handle_errors
def recipients_delete(c: Ctx, account_id, yes) -> None:
    confirm_or_abort(c, yes, f"Delete recipient {account_id}?")
    api.delete_recipient(c.client, account_id)
    out.emit_json({"ok": True, "deleted": account_id}) if c.json else click.secho(f"✓ Recipient {account_id} deleted", fg="green")


# ── transfers ─────────────────────────────────────────────────────────


@cli.group()
def transfers() -> None:
    """Create, fund, track and cancel transfers."""


@transfers.command("list")
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--offset", default=0, type=int)
@click.option("--status", default=None, help="e.g. incoming_payment_waiting,processing,outgoing_payment_sent")
@click.option("--since", default=None, help="Created after (ISO date).")
@click.option("--until", default=None, help="Created before (ISO date).")
@profile_option
@pass_ctx
@handle_errors
def transfers_list(c: Ctx, limit, offset, status, since, until, profile_id) -> None:
    data = api.list_transfers(c.client, c.profile_id(profile_id), limit, offset, status, since, until)
    if c.json:
        return out.emit_json(data)
    out.table(["ID", "CREATED", "STATUS", "SEND", "RECEIVE", "REFERENCE"], [
        [t.get("id"), str(t.get("created", ""))[:16], t.get("status"),
         out.fmt_money(t.get("sourceValue"), t.get("sourceCurrency")),
         out.fmt_money(t.get("targetValue"), t.get("targetCurrency")),
         t.get("reference") or (t.get("details") or {}).get("reference", "")]
        for t in data
    ])


@transfers.command("get")
@click.argument("transfer_id", type=int)
@pass_ctx
@handle_errors
def transfers_get(c: Ctx, transfer_id) -> None:
    data = api.get_transfer(c.client, transfer_id)
    out.emit_json(data) if c.json else out.kv(data, f"Transfer {transfer_id}")


@transfers.command("send")
@click.argument("amount", type=float)
@click.argument("source")
@click.argument("target")
@click.option("--to", "recipient_id", type=int, required=True, help="Recipient account id.")
@click.option("--reference", default=None, help="Payment reference shown to the recipient.")
@click.option("--target-amount", is_flag=True, help="Treat AMOUNT as the amount the recipient gets.")
@click.option("--fund/--no-fund", default=False, show_default=True,
              help="Also pay the transfer from your Wise balance.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Quote only, create nothing.")
@profile_option
@pass_ctx
@handle_errors
def transfers_send(c: Ctx, amount, source, target, recipient_id, reference, target_amount,
                   fund, yes, dry_run, profile_id) -> None:
    """Quote → create transfer (→ optionally fund from balance) in one step."""
    pid = c.profile_id(profile_id)
    body = api.build_quote_body(source, target,
                                None if target_amount else amount,
                                amount if target_amount else None,
                                recipient_id)
    quote = api.create_quote(c.client, pid, body)
    summary = _quote_summary(quote)
    summary["recipient id"] = recipient_id
    summary["reference"] = reference or ""
    if dry_run:
        return out.emit_json({"dry_run": True, "quote": quote,
                              "transfer_request": api.build_transfer_body(recipient_id, quote["id"], reference)}) \
            if c.json else out.kv(summary, "Transfer preview (dry run)")
    if not c.json:
        out.kv(summary, "Transfer preview")
    action = "Create AND FUND" if fund else "Create"
    confirm_or_abort(c, yes, f"{action} transfer of {summary['you send']} → {summary['recipient gets']}?")
    transfer = api.create_transfer(c.client, api.build_transfer_body(recipient_id, quote["id"], reference))
    result: dict[str, Any] = {"quote": quote, "transfer": transfer}
    if fund:
        result["payment"] = api.fund_transfer(c.client, pid, transfer["id"])
    if c.json:
        return out.emit_json(result)
    click.secho(f"✓ Transfer {transfer.get('id')} created (status: {transfer.get('status')})", fg="green")
    if fund:
        p = result["payment"]
        click.secho(f"✓ Funding: {p.get('status')} {p.get('errorCode') or ''}".rstrip(), fg="green")
    else:
        click.echo(f"  Fund it with: cli-anything-wise transfers fund {transfer.get('id')}")


@transfers.command("fund")
@click.argument("transfer_id", type=int)
@click.option("--method", default="BALANCE", show_default=True)
@click.option("--yes", is_flag=True)
@profile_option
@pass_ctx
@handle_errors
def transfers_fund(c: Ctx, transfer_id, method, yes, profile_id) -> None:
    """Pay a created transfer from your Wise balance."""
    confirm_or_abort(c, yes, f"Fund transfer {transfer_id} from {method}?")
    data = api.fund_transfer(c.client, c.profile_id(profile_id), transfer_id, method)
    out.emit_json(data) if c.json else out.kv(data, "Funding result")


@transfers.command("cancel")
@click.argument("transfer_id", type=int)
@click.option("--yes", is_flag=True)
@pass_ctx
@handle_errors
def transfers_cancel(c: Ctx, transfer_id, yes) -> None:
    confirm_or_abort(c, yes, f"Cancel transfer {transfer_id}?")
    data = api.cancel_transfer(c.client, transfer_id)
    out.emit_json(data) if c.json else click.secho(f"✓ Transfer {transfer_id}: {data.get('status')}", fg="green")


@transfers.command("eta")
@click.argument("transfer_id", type=int)
@pass_ctx
@handle_errors
def transfers_eta(c: Ctx, transfer_id) -> None:
    """Estimated delivery time."""
    data = api.delivery_estimate(c.client, transfer_id)
    out.emit_json(data) if c.json else out.kv(data, f"Delivery estimate for {transfer_id}")


@transfers.command("receipt")
@click.argument("transfer_id", type=int)
@click.option("--output", "-o", "output_path", type=click.Path(dir_okay=False), default=None)
@pass_ctx
@handle_errors
def transfers_receipt(c: Ctx, transfer_id, output_path) -> None:
    """Download the PDF receipt."""
    pdf = api.transfer_receipt_pdf(c.client, transfer_id)
    path = Path(output_path or f"wise-transfer-{transfer_id}.pdf")
    path.write_bytes(pdf)
    out.emit_json({"ok": True, "file": str(path), "bytes": len(pdf)}) if c.json \
        else click.secho(f"✓ Saved {path}", fg="green")


# ── activity ──────────────────────────────────────────────────────────


@cli.command("activity")
@click.option("--size", default=20, show_default=True, type=int)
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--status", default=None)
@click.option("--cursor", default=None, help="nextCursor from a previous page.")
@profile_option
@pass_ctx
@handle_errors
def activity(c: Ctx, size, since, until, status, cursor, profile_id) -> None:
    """Recent account activity (card payments, transfers, conversions…)."""
    data = api.list_activities(c.client, c.profile_id(profile_id), size, since, until, status, cursor)
    if c.json:
        return out.emit_json(data)
    import re
    strip = lambda s: re.sub(r"<[^>]+>", "", s or "")  # noqa: E731
    out.table(["DATE", "TYPE", "STATUS", "TITLE", "AMOUNT"], [
        [str(a.get("createdOn", ""))[:16], a.get("type"), a.get("status"),
         strip(a.get("title")), strip(a.get("primaryAmount"))]
        for a in data.get("activities", [])
    ])
    if data.get("cursor"):
        click.echo(f"\n  next page: --cursor {data['cursor']}")


# ── REPL ──────────────────────────────────────────────────────────────


@cli.command(hidden=True)
@click.pass_context
def repl(ctx: click.Context) -> None:
    """Interactive shell."""
    from .utils.repl_skin import ReplSkin

    c: Ctx = ctx.obj
    skin = ReplSkin("wise", version=__version__)
    skin.print_banner()
    skin.info(f"environment: {c.settings.env}  ·  token: {cfg.mask(c.settings.token)}")
    skin.hint("Type 'help' for commands, 'exit' to quit.")
    session = None
    if sys.stdin.isatty():
        try:
            session = skin.create_prompt_session()
        except Exception:  # pragma: no cover - terminal quirks
            session = None

    global_args: list[str] = []
    if c.json:
        global_args.append("--json")
    if c.settings.token:
        global_args += ["--token", c.settings.token]
    global_args += ["--env", c.settings.env]
    if c.settings.profile_id:
        global_args += ["--profile", str(c.settings.profile_id)]

    while True:
        try:
            line = skin.get_input(session, project_name=c.settings.env)
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit", "q"):
            break
        if line == "help":
            skin.help({name: (cmd.get_short_help_str(60)) for name, cmd in cli.commands.items()
                       if not cmd.hidden})
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            skin.error(str(exc))
            continue
        try:
            cli.main(args=global_args + args, prog_name="wise", standalone_mode=False)
        except click.exceptions.Exit:
            pass
        except click.ClickException as exc:
            skin.error(exc.format_message())
        except click.Abort:
            skin.warning("Aborted.")
        except SystemExit:
            pass
    skin.print_goodbye()


def main() -> None:
    cli(prog_name="cli-anything-wise")


if __name__ == "__main__":
    main()
