"""環境変数の読み込み。pydantic-settings を使う。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    line_channel_access_token: str = ""
    line_channel_secret: str = ""
    line_user_id: str = ""  # カンマ区切りで複数可
    gemini_api_key: str = ""
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    trigger_token: str = ""

    @property
    def line_user_ids(self) -> list[str]:
        """push 先のユーザーID一覧。カンマ区切りを分解する。"""
        return [u.strip() for u in self.line_user_id.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
