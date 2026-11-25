# cli-anything-wise

Command-line access to your **Wise** (ex-TransferWise) account through the
official [Wise Platform API](https://docs.wise.com/api-reference): balances,
exchange rates, quotes, recipients, transfers, statements and activity — for
humans (tables, interactive REPL) and AI agents (`--json` everywhere).

## Install

```bash
pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=wise/agent-harness
# optional, to sign Strong Customer Authentication challenges:
pip install "cli-anything-wise[sca] @ git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=wise/agent-harness"
```

## Get a token

1. Wise web app → **Settings → Integrations and tools → API tokens** (2-step login must be on).
2. Create a token (read-only is enough to look around; *full access* to create transfers).
3. For testing, create a free account on the sandbox at <https://wise-sandbox.com> and take a token there.

```bash
export WISE_API_TOKEN=xxxxxxxx           # or: cli-anything-wise config set --token xxxxxxxx
export WISE_ENV=production               # default is sandbox — real money only when you say so
cli-anything-wise whoami
```

Settings are resolved CLI flag → environment variable → config file
(`~/.config/cli-anything-wise/config.json`, written with `chmod 600`).

| Variable | Meaning |
|---|---|
| `WISE_API_TOKEN` | personal API token |
| `WISE_ENV` | `sandbox` (default) or `production` |
| `WISE_PROFILE_ID` | profile to use (default: your PERSONAL profile) |
| `WISE_PRIVATE_KEY_PATH` | PEM private key for SCA signing |

## Examples

```bash
cli-anything-wise profiles list
cli-anything-wise balances list
cli-anything-wise rates EUR USD
cli-anything-wise quotes create 250 EUR GBP
cli-anything-wise recipients add-iban --name "Anna Bianchi" --iban IT60X0542811101000000123456
cli-anything-wise transfers send 250 EUR EUR --to 123456 --reference "Affitto" --dry-run
cli-anything-wise transfers send 250 EUR EUR --to 123456 --reference "Affitto"     # asks for confirmation
cli-anything-wise transfers list --limit 10
cli-anything-wise transfers receipt 987654 -o receipt.pdf
cli-anything-wise balances convert 100 EUR USD --dry-run
cli-anything-wise balances statement 555 --currency EUR --from 2026-10-01 --to 2026-10-31 --format csv
cli-anything-wise activity --size 30
cli-anything-wise --json balances list | jq '.[] | {currency, amount: .amount.value}'
cli-anything-wise            # interactive REPL
```

## Commands

| Group | Commands |
|---|---|
| `config` | `show`, `set`, `clear` |
| `whoami` | verify token |
| `profiles` | `list`, `get` |
| `balances` | `list`, `get`, `open`, `close`, `convert`, `statement` |
| `rates`, `currencies` | live/historic rates, supported currencies |
| `quotes` | `create`, `get` |
| `recipients` | `list`, `get`, `add-iban`, `add-email`, `add-raw`, `requirements`, `delete` |
| `transfers` | `list`, `get`, `send`, `fund`, `cancel`, `eta`, `receipt` |
| `activity` | recent account activity with cursor paging |

## Safety

* **Sandbox by default.** Production needs `--env production` / `WISE_ENV=production`.
* Every command that moves or cancels money (`transfers send/fund/cancel`,
  `balances convert/close`, `recipients delete`) asks for confirmation; in
  `--json` or non-interactive mode it refuses unless `--yes` is passed.
* `--dry-run` on `transfers send` and `balances convert` only creates a quote.
* The token is never printed (`config show` masks it).

## Limits of personal tokens (PSD2)

Wise restricts personal tokens. For profiles registered in the **EU/EEA or UK**,
*funding a transfer* and *balance statements* require Strong Customer
Authentication and are generally **not available** with a personal token
(they are for US, CA, AU, NZ, SG, MY). In that case:

* create the transfer with the CLI (`transfers send` without `--fund`), then
  confirm/pay it in the Wise app;
* if Wise sends an SCA challenge your token can satisfy, upload a public key
  in Wise (*API tokens → Manage public keys*) and set `WISE_PRIVATE_KEY_PATH`;
  the CLI signs the one-time token and retries automatically.

```bash
openssl genrsa -out wise-private.pem 2048
openssl rsa -pubout -in wise-private.pem -out wise-public.pem   # upload this one
```

## Tests

```bash
pip install -e ".[dev]"
pytest cli_anything/wise/tests/test_core.py -v            # offline, mocked HTTP
WISE_API_TOKEN=<sandbox token> pytest cli_anything/wise/tests/test_full_e2e.py -v
```
