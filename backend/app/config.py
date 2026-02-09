from pathlib import Path
from pydantic_settings import BaseSettings

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    DATABASE_URL: str = f"sqlite:///{_BACKEND_DIR / 'stock_valuation.db'}"
    DATA_DIR: Path = _PROJECT_ROOT / "data"
    PROJECT_ROOT: Path = _PROJECT_ROOT
    CURRENT_FISCAL_YEAR: int = 2025
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]


settings = Settings()
