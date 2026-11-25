"""Configuration handling for cli-anything-wise.

Resolution order for every setting (first match wins):

1. explicit CLI option (``--token``, ``--env``, ``--profile``)
2. environment variable (``WISE_API_TOKEN``, ``WISE_ENV``, ``WISE_PROFILE_ID``,
   ``WISE_PRIVATE_KEY_PATH``)
3. config file (``$XDG_CONFIG_HOME/cli-anything-wise/config.json``)
4. built-in default (environment = ``sandbox``)

The default environment is **sandbox** on purpose: nothing touches real money
unless the user explicitly opts in to ``production``.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENVIRONMENTS = {
    "production": "https://api.wise.com",
    "sandbox": "https://api.wise-sandbox.com",
}
DEFAULT_ENV = "sandbox"

CONFIG_KEYS = ("token", "env", "profile_id", "private_key_path")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cli-anything-wise"


def config_path() -> Path:
    override = os.environ.get("WISE_CONFIG_FILE")
    return Path(override) if override else config_dir() / "config.json"


def load_file() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_file(data: dict[str, Any]) -> Path:
    """Write the config file with owner-only permissions (it may hold a token)."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if k in CONFIG_KEYS and v not in (None, "")}
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    return path


def mask(token: str | None) -> str:
    if not token:
        return "(not set)"
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}…{token[-4:]}"


@dataclass
class Settings:
    token: str | None = None
    env: str = DEFAULT_ENV
    profile_id: int | None = None
    private_key_path: str | None = None
    base_url: str = field(default="")

    def __post_init__(self) -> None:
        if self.env not in ENVIRONMENTS:
            raise ValueError(
                f"Unknown environment '{self.env}'. Use one of: {', '.join(ENVIRONMENTS)}"
            )
        if not self.base_url:
            self.base_url = ENVIRONMENTS[self.env]

    def public_view(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "base_url": self.base_url,
            "token": mask(self.token),
            "profile_id": self.profile_id,
            "private_key_path": self.private_key_path,
            "config_file": str(config_path()),
        }


def resolve(
    token: str | None = None,
    env: str | None = None,
    profile_id: int | None = None,
) -> Settings:
    file_cfg = load_file()

    def pick(cli_val: Any, env_name: str, file_key: str) -> Any:
        if cli_val not in (None, ""):
            return cli_val
        env_val = os.environ.get(env_name)
        if env_val:
            return env_val
        return file_cfg.get(file_key)

    raw_profile = pick(profile_id, "WISE_PROFILE_ID", "profile_id")
    resolved_env = (pick(env, "WISE_ENV", "env") or DEFAULT_ENV).lower()
    # WISE_BASE_URL is an escape hatch for tests / mock servers only.
    base_url = os.environ.get("WISE_BASE_URL") or ""
    return Settings(
        token=pick(token, "WISE_API_TOKEN", "token"),
        env=resolved_env,
        profile_id=int(raw_profile) if raw_profile not in (None, "") else None,
        private_key_path=pick(None, "WISE_PRIVATE_KEY_PATH", "private_key_path"),
        base_url=base_url,
    )
