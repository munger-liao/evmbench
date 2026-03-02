from pydantic import Field, Secret
from pydantic_settings import BaseSettings, SettingsConfigDict

from api.util.fs import ROOT_DIR


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / '.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    OAI_PROXY_HOST: str = '127.0.0.1'
    OAI_PROXY_PORT: int = 8084
    OAI_PROXY_WORKERS: int = 1
    OAI_PROXY_AES_KEY: Secret[str]
    # Static OpenAI key - when set, requests with "Bearer STATIC" use this key
    # The real key never leaves this service
    OAI_PROXY_STATIC_KEY: Secret[str] | None = None
    # OpenAI API base URL - configurable for custom endpoints (e.g., Azure OpenAI, local LLMs)
    OAI_PROXY_OPENAI_BASE_URL: str = 'https://api.openai.com'
    # Per-model route overrides: {"model_name": {"base_url": "...", "api_key": "..."}}
    OAI_PROXY_MODEL_ROUTES: dict[str, dict[str, str]] = Field(default_factory=dict)


settings = Settings()  # type: ignore[missing-argument]
