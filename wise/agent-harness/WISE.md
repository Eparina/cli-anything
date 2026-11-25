# Wise CLI harness — SOP

## Target

Wise (wise.com) is a SaaS money-transfer / multi-currency account service. It
has no local binary, so the "backend" is the **Wise Platform REST API**
(`https://api.wise.com`, sandbox `https://api.wise-sandbox.com`), authenticated
with a personal API token (Bearer).

## Architecture

```
cli_anything/wise/
├── wise_cli.py        click command tree + REPL (entry point cli-anything-wise)
├── core/config.py     settings resolution (flag → env → ~/.config file), sandbox default
├── core/client.py     requests wrapper, error mapping, SCA one-time-token signing + retry
├── core/api.py        one function per endpoint + pure request-body builders
├── utils/output.py    JSON / table / key-value rendering
└── utils/repl_skin.py unmodified copy from cli-anything-plugin
```

Endpoints used

| Area | Endpoint |
|---|---|
| user / profiles | `GET /v1/me`, `GET /v2/profiles`, `GET /v2/profiles/{id}` |
| balances | `GET/POST /v4/profiles/{p}/balances`, `GET/DELETE …/balances/{id}` |
| conversion | `POST /v2/profiles/{p}/balance-movements` (quote with `payOut=BALANCE`) |
| statements | `GET /v1/profiles/{p}/balance-statements/{b}/statement.{json,csv,pdf,…}` |
| rates | `GET /v1/rates`, `GET /v1/currencies` |
| quotes | `POST /v3/profiles/{p}/quotes`, `GET …/quotes/{id}` |
| recipients | `GET /v2/accounts`, `GET /v2/accounts/{id}`, `POST /v1/accounts`, `DELETE /v1/accounts/{id}`, `GET /v1/quotes/{id}/account-requirements` |
| transfers | `GET/POST /v1/transfers`, `GET /v1/transfers/{id}`, `PUT …/cancel`, `GET …/receipt.pdf`, `GET /v1/delivery-estimates/{id}`, `POST /v3/profiles/{p}/transfers/{id}/payments` |
| activity | `GET /v1/profiles/{p}/activities` |

## Design decisions

* **Stateless**: every invocation resolves settings and talks to the API; the
  only persisted state is the optional config file (mode 600).
* **Safe by default**: sandbox environment, confirmation on money movement,
  `--dry-run`, refusal to act unconfirmed in non-interactive/JSON mode.
* **Agent-friendly**: `--json` everywhere, errors as `{"ok": false, "error", "status", "details"}` on stderr with exit code 1.
* **Idempotency**: transfers carry a fresh `customerTransactionId`; balance
  creation / conversion send `X-idempotence-uuid`.
* **SCA**: 403 + `x-2fa-approval-result: REJECTED` → sign `x-2fa-approval`
  one-time token with SHA256withRSA, retry with `X-Signature`.

## Known limits

Personal tokens of EU/UK profiles cannot fund transfers nor download
statements (PSD2). The CLI surfaces Wise's error verbatim and the README
explains the workaround (create via CLI, pay in the app).
