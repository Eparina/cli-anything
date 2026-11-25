"""End-to-end tests against the real Wise **sandbox** API.

They are skipped unless a sandbox token is provided:

    export WISE_API_TOKEN=<sandbox personal token>   # from wise-sandbox.com
    pytest cli_anything/wise/tests/test_full_e2e.py -v

The tests never run against production: WISE_ENV is forced to ``sandbox``.
They are read-only apart from creating a quote and a dry-run transfer preview.
The subprocess test at the bottom only needs the package installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest
from click.testing import CliRunner

from cli_anything.wise import wise_cli

TOKEN = os.environ.get("WISE_API_TOKEN")
needs_sandbox = pytest.mark.skipif(not TOKEN, reason="WISE_API_TOKEN (sandbox) not set")


def wise(*args: str):
    env = {**os.environ, "WISE_ENV": "sandbox"}
    r = CliRunner(env=env).invoke(wise_cli.cli, ["--env", "sandbox", "--json", *args])
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


@needs_sandbox
class TestSandbox:
    def test_whoami(self):
        me = wise("whoami")
        assert "id" in me

    def test_profiles(self):
        profiles = wise("profiles", "list")
        assert isinstance(profiles, list) and profiles
        assert {"id", "type"} <= set(profiles[0])

    def test_balances(self):
        balances = wise("balances", "list")
        assert isinstance(balances, list)

    def test_rates(self):
        rates = wise("rates", "EUR", "GBP")
        assert rates and rates[0]["rate"] > 0

    def test_quote(self):
        q = wise("quotes", "create", "100", "GBP", "EUR")
        assert q["sourceCurrency"] == "GBP" and q["targetCurrency"] == "EUR"
        again = wise("quotes", "get", q["id"])
        assert again["id"] == q["id"]

    def test_recipients_and_transfers_list(self):
        assert isinstance(wise("recipients", "list"), list)
        assert isinstance(wise("transfers", "list", "--limit", "5"), list)

    def test_send_dry_run(self):
        recips = wise("recipients", "list", "--currency", "EUR")
        if not recips:
            pytest.skip("no EUR recipient in sandbox profile")
        out = wise("transfers", "send", "10", "GBP", "EUR", "--to", str(recips[0]["id"]), "--dry-run")
        assert out["dry_run"] is True


def _resolve_cli() -> list[str]:
    exe = shutil.which("cli-anything-wise")
    if exe:
        return [exe]
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED"):
        pytest.fail("cli-anything-wise not on PATH")
    return [sys.executable, "-m", "cli_anything.wise"]


class TestCLISubprocess:
    def test_help(self):
        r = subprocess.run([*_resolve_cli(), "--help"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0
        assert "transfers" in r.stdout

    def test_version(self):
        r = subprocess.run([*_resolve_cli(), "--version"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "1.0.0" in r.stdout

    def test_missing_token_is_clean_json_error(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if not k.startswith("WISE_")}
        env["WISE_CONFIG_FILE"] = str(tmp_path / "none.json")
        r = subprocess.run([*_resolve_cli(), "--json", "whoami"], capture_output=True, text=True,
                           timeout=60, env=env)
        assert r.returncode == 1
        assert json.loads(r.stderr)["ok"] is False
