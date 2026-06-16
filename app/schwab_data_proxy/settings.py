from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SCHWAB_DATA_APP_KEY: str = ""
    SCHWAB_DATA_APP_SECRET: str = ""
    SCHWAB_DATA_TOKEN_PATH: str = "/data/token.json"
    SCHWAB_DATA_CALLBACK_URL: str = ""
    # Optional — separate Schwab Trader API app credentials.
    # If set, the streaming client uses these instead of the main credentials.
    SCHWAB_TRADER_APP_KEY: str = ""
    SCHWAB_TRADER_APP_SECRET: str = ""
    SCHWAB_TRADER_TOKEN_PATH: str = "/data/trader_token.json"
    SCHWAB_TRADER_CALLBACK_URL: str = ""
    PORT: int = 8080
    CACHE_TTL_SECONDS: int = 2
    LOG_LEVEL: str = "INFO"
    # Set to true in CI to skip Schwab session init so /healthz is testable
    SCHWAB_SKIP_INIT: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
