"""Output helpers: JSON for agents, compact tables for humans."""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Sequence

import click


def emit_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def emit_error(message: str, as_json: bool, details: Any = None, status: int | None = None) -> None:
    if as_json:
        payload: dict[str, Any] = {"ok": False, "error": message}
        if status is not None:
            payload["status"] = status
        if details not in (None, "", {}):
            payload["details"] = details
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str), err=True)
    else:
        prefix = f"HTTP {status}: " if status else ""
        click.secho(f"✗ {prefix}{message}", fg="red", err=True)


def fmt_money(value: Any, currency: str | None = None) -> str:
    if isinstance(value, dict):
        currency = value.get("currency", currency)
        value = value.get("value")
    if value is None:
        return "-"
    try:
        num = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        num = str(value)
    return f"{num} {currency}" if currency else num


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    rows = [["" if v is None else str(v) for v in r] for r in rows]
    if not rows:
        click.echo("(no results)")
        return
    widths = [len(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(v))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    click.secho(line, bold=True)
    click.echo("  ".join("─" * w for w in widths))
    for r in rows:
        click.echo("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)))


def kv(data: dict[str, Any], title: str | None = None) -> None:
    if title:
        click.secho(title, bold=True)
    if not data:
        click.echo("(empty)")
        return
    width = max(len(str(k)) for k in data)
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, default=str)
        click.echo(f"  {str(k).ljust(width)}  {v}")


def is_tty() -> bool:
    return sys.stdout.isatty()
