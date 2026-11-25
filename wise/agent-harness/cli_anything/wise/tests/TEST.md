# Test plan — cli-anything-wise

## Unit (`test_core.py`, offline)

HTTP is replaced by a fake `requests.Session`, so no account or network is needed.

- **Config**: sandbox default, precedence flag > env > file, file written 0600, unknown env rejected, token masking, `WISE_BASE_URL` override.
- **Client**: Bearer + User-Agent headers, missing token → 401, Wise error payload → readable message, network errors wrapped, `None` params dropped, SCA challenge without key explained, SCA challenge signed (verified with the public key) and replayed.
- **API helpers**: default profile selection, quote body validation, payment option choice, IBAN normalisation, unique `customerTransactionId`, statement date normalisation, `/v2/accounts` unwrapping.
- **CLI**: help, config set/show, JSON errors, balances table, rates JSON, quote summary, `send --dry-run` creates nothing, `send` refuses without `--yes` in JSON mode and when the user answers "n", `send --fund --yes` request chain, cancel, add-iban, API error exit code, receipt file, activity HTML stripping, REPL dispatch.

## E2E (`test_full_e2e.py`)

- Subprocess tests of the installed `cli-anything-wise` binary (`--help`, `--version`, clean JSON error without token) — always run.
- Sandbox tests (whoami, profiles, balances, rates, quote create/get, recipients/transfers list, send dry-run) — run only with `WISE_API_TOKEN` set to a **sandbox** token; forced to `--env sandbox`, never touch production.

## Results

```
$ CLI_ANYTHING_FORCE_INSTALLED=1 pytest cli_anything/wise/tests -q
40 passed, 7 skipped   # sandbox tests skipped: no sandbox token in CI
```
