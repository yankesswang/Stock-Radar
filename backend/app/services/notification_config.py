"""Resolve notification credentials without coupling delivery code to file layout."""

import os
from pathlib import Path

from ..config import settings


TELEGRAM_ENV_FALLBACKS = (
    Path("/Users/yankesswang/Desktop/Projects/Arthur_App/.env"),
    Path("/Users/yankesswang/openclaw_backup/.env"),
)
LINE_ENV_FALLBACKS = (
    *TELEGRAM_ENV_FALLBACKS,
    Path("/Users/yankesswang/.config/digest/.env"),
    Path("/Users/yankesswang/.hn-daily-digest/.env"),
)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def telegram_config() -> tuple[str, str]:
    token = settings.TELEGRAM_TOKEN.strip()
    chat_id = settings.TELEGRAM_CHAT_ID.strip() or settings.TELEGRAM_ADMIN_ID.strip()
    if token and chat_id:
        return token, chat_id

    env_paths = (Path(settings.TELEGRAM_ENV_FILE).expanduser(), *TELEGRAM_ENV_FALLBACKS)
    for env_path in env_paths:
        env_values = read_env_file(env_path)
        if not env_values:
            continue
        token = token or env_values.get("TELEGRAM_TOKEN", "").strip()
        chat_id = (
            chat_id
            or env_values.get("TELEGRAM_CHAT_ID", "").strip()
            or env_values.get("TELEGRAM_ADMIN_ID", "").strip()
            or env_values.get("ADMIN_ID", "").strip()
        )
        if token and chat_id:
            break
    return token, chat_id


def line_access_token() -> str:
    token = (
        os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        or os.environ.get("LINE_ACCESS_TOKEN", "").strip()
        or os.environ.get("CHANNEL_ACCESS_TOKEN", "").strip()
    )
    if token:
        return token

    env_paths = (Path(settings.TELEGRAM_ENV_FILE).expanduser(), *LINE_ENV_FALLBACKS)
    for env_path in env_paths:
        env_values = read_env_file(env_path)
        token = (
            env_values.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
            or env_values.get("LINE_ACCESS_TOKEN", "").strip()
            or env_values.get("CHANNEL_ACCESS_TOKEN", "").strip()
        )
        if token:
            return token
    return ""
