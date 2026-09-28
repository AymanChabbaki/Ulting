"""Settings, loaded from the repo-root .env.local."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env.local", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    graph_api_version: str = "v23.0"
    meta_allowed_accounts: str = ""

    # Auth. The dashboard exposes an ads_management token, so it is never
    # served without a login -- see auth.py for why these two must be set.
    dashboard_user: str = ""
    dashboard_password: str = ""
    session_secret: str = ""
    session_hours: int = 12
    session_cookie_secure: bool = False

    # Strategist (OpenAI). Without the key the chat route returns a clear
    # error rather than failing at the first request. The model is configurable
    # so it can be changed without touching code.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    # Creative generation. Values must come from the SDK's own enums --
    # ImageModel and VideoModel in openai.types.
    openai_image_model: str = "gpt-image-1"
    openai_video_model: str = "sora-2"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def allowed_accounts(self) -> list[str]:
        return [a.strip() for a in self.meta_allowed_accounts.split(",") if a.strip()]

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
