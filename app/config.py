from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/newstrader"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-reasoner"
    llm_request_timeout: float = 120.0
    moex_base_url: str = "https://iss.moex.com/iss"
    telegram_bot_token: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_channels: str = ""
    telegram_session_name: str = "telethon_session"
    admin_usernames: str = ""
    script_timeout_seconds: int = 7200  # таймаут запуска скрипта из админки (env SCRIPT_TIMEOUT_SECONDS)
    app_url: str = "http://localhost:8000"
    mvp_tickers: str = "AFLT,LKOH,GAZP,SBER"
    auto_create_schema: bool = True

    @property
    def ticker_list(self) -> list[str]:
        return [t.strip().upper() for t in self.mvp_tickers.split(",") if t.strip()]

    @property
    def telegram_channel_list(self) -> list[str]:
        return [
            c.strip().lstrip("@") for c in self.telegram_channels.split(",") if c.strip()
        ]

    @property
    def admin_username_list(self) -> list[str]:
        return [u.strip() for u in self.admin_usernames.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
