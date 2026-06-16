from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SCHWAB_APP_KEY: str = ""
    SCHWAB_APP_SECRET: str = ""
    SCHWAB_TOKEN_PATH: str = "/data/token.json"
    SCHWAB_CALLBACK_URL: str = ""
    # Optional — separate Schwab Trader API app credentials.
    # If set, the streaming client uses these instead of the main credentials.
    # Callback URL is reused from SCHWAB_CALLBACK_URL.
    SCHWAB_TRADER_APP_KEY: str = ""
    SCHWAB_TRADER_APP_SECRET: str = ""
    SCHWAB_TRADER_TOKEN_PATH: str = "/data/trader_token.json"
    PORT: int = 8080
    CACHE_TTL_SECONDS: int = 2
    LOG_LEVEL: str = "INFO"
    # Set to true in CI to skip Schwab session init so /healthz is testable
    SCHWAB_SKIP_INIT: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
