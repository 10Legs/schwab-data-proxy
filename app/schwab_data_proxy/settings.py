from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SCHWAB_APP_KEY: str
    SCHWAB_APP_SECRET: str
    SCHWAB_TOKEN_PATH: str = "/data/token.json"
    SCHWAB_CALLBACK_URL: str
    PORT: int = 8080
    CACHE_TTL_SECONDS: int = 2
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
