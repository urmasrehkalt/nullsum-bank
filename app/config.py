from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BANK_NAME: str = "NullSum Bank"
    BANK_ADDRESS: str = "http://localhost:8000"
    CENTRAL_BANK_URL: str = "https://test.diarainfra.com/central-bank"
    SECRET_KEY: str = "change-me"
    DB_PATH: str = "./bank.db"
    KEYS_DIR: str = "./keys"
    ACCESS_TOKEN_EXPIRE_DAYS: int = 30
    # Optional: set to skip central bank registration (useful for local testing)
    BANK_ID: str = ""


settings = Settings()
