"""Backend configuration — env vars + defaults via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str
    context7_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    max_agent_steps: int = 10
    policy_rules_path: str = "policy_rules.yaml"


@lru_cache
def get_settings() -> Settings:
    # ponytail: settings themselves are static per-process (env vars), so
    # caching this accessor is fine — it's the policy RULES that must never
    # be cached (see policy_engine.load_rules), not the config object.
    return Settings()
