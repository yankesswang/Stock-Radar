from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    # Load from backend/.env if present (never committed to git)
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = f"sqlite:///{_BACKEND_DIR / 'stock_valuation.db'}"
    DATA_DIR: Path = _PROJECT_ROOT / "data"
    PROJECT_ROOT: Path = _PROJECT_ROOT
    CURRENT_FISCAL_YEAR: int = 2025
    CORS_ORIGINS: list[str] = ["http://localhost:5000", "http://localhost:5173", "http://localhost:3000"]

    # Google Sheet IDs for portfolio sync (set in backend/.env)
    GSHEET_ID: str = ""
    GSHEET_MOM_ID: str = ""

    # Optional: write local portfolio.csv changes back to Google Sheets (bi-directional sync)
    GSHEET_WRITEBACK_ENABLED: bool = False
    GSHEET_WRITEBACK_SPREADSHEET_ID: str = ""   # default to GSHEET_ID when empty
    GSHEET_WRITEBACK_WORKSHEET: str = ""        # default to first worksheet when empty
    GSHEET_SERVICE_ACCOUNT_FILE: str = ""       # path to service-account json
    GSHEET_SERVICE_ACCOUNT_JSON: str = ""       # raw service-account json string

    # Telegram notification settings. Falls back to AI Digest's ~/.hn-daily-digest/.env.
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_ADMIN_ID: str = ""
    TELEGRAM_ENV_FILE: str = "~/.hn-daily-digest/.env"

    # Claude API — daily post-market analysis
    ANTHROPIC_API_KEY: str = ""
    MIMI_DOC_PATH: str = "/Users/yankesswang/Documents/arthurwang_DB/投資/mimi操作建議總表.md"


settings = Settings()
