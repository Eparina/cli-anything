---
name: "cli-anything-wise"
description: "Wise (TransferWise) CLI - check balances, exchange rates and quotes, manage recipients, create/fund/cancel transfers and read account activity via the Wise Platform API. Use when the user wants to inspect or operate their Wise account from the terminal."
version: "1.0.0"
author: "CLI-Anything Community"
---

# cli-anything-wise

Operate a Wise account through the official Wise Platform API.

## Setup

```bash
pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=wise/agent-harness
export WISE_API_TOKEN=<personal token>   # Wise → Settings → Integrations and tools → API tokens
export WISE_ENV=production               # default is sandbox
cli-anything-wise --json whoami
```

## Agent rules

- Always pass `--json`; parse stdout. Errors go to stderr as `{"ok": false, "error", "status", "details"}` with exit code 1.
- Default environment is **sandbox**. Never switch to production unless the user asked for it.
- Money-moving commands (`transfers send|fund|cancel`, `balances convert|close`, `recipients delete`) refuse to run in `--json` mode without `--yes`. **Always run them first with `--dry-run` (where available), show the user the amounts/fees, and only add `--yes` after the user explicitly confirms.**
- Amount arguments are the amount sent; add `--target-amount` to fix the amount received.
- Profile defaults to the PERSONAL profile; use `--profile <id>` (from `profiles list`) for a business profile.
- EU/UK personal tokens cannot fund transfers or fetch statements (PSD2). If `transfers fund` fails with an SCA/403 error, tell the user to complete payment in the Wise app.

## Commands

| Command | Purpose |
|---|---|
| `config show` / `config set --token T --env E --profile P --private-key K` / `config clear` | settings |
| `whoami` | verify token |
| `profiles list` / `profiles get ID` | profiles |
| `balances list [--type STANDARD,SAVINGS]` | balances with amounts |
| `balances get ID` / `balances open CUR [--jar NAME]` / `balances close ID` | manage balances |
| `balances convert AMOUNT SRC TGT [--dry-run] [--yes]` | convert between own balances |
| `balances statement ID --currency C [--from D --to D --format json/csv/pdf]` | statement |
| `rates [SRC] [TGT] [--time ISO]` / `currencies` | FX data |
| `quotes create AMOUNT SRC TGT [--recipient ID] [--target-amount]` / `quotes get ID` | price a transfer |
| `recipients list [--currency C]` / `recipients get ID` | recipients |
| `recipients add-iban --name N --iban I [--currency EUR] [--business]` | add bank recipient |
| `recipients add-email --name N --email E --currency C` | add e-mail recipient |
| `recipients requirements QUOTE_ID` / `recipients add-raw FILE.json` | other recipient types |
| `recipients delete ID [--yes]` | remove recipient |
| `transfers list [--limit --status --since --until]` / `transfers get ID` | history |
| `transfers send AMOUNT SRC TGT --to RECIPIENT [--reference R] [--fund] [--dry-run] [--yes]` | quote + create (+ fund) |
| `transfers fund ID [--yes]` / `transfers cancel ID [--yes]` | pay / cancel |
| `transfers eta ID` / `transfers receipt ID -o file.pdf` | delivery estimate, receipt |
| `activity [--size N --since ISO --cursor C]` | recent activity |

## Typical flow: send money

```bash
cli-anything-wise --json recipients list --currency EUR
cli-anything-wise --json transfers send 100 EUR EUR --to 123 --reference "Invoice 42" --dry-run
# show quote to user, wait for explicit confirmation, then:
cli-anything-wise --json transfers send 100 EUR EUR --to 123 --reference "Invoice 42" --yes
```
