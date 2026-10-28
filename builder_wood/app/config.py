from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DEBUG: bool = False
    APP_NAME: str = "Builder Wood"

    # Application security (REQUIRED — set in .env / Render env vars)
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # Paystack (REQUIRED — set in .env / Render env vars)
    PAYSTACK_SECRET_KEY: str

    # Application URL
    BASE_URL: str = "http://127.0.0.1:8000"

    # Aiven MySQL database
    DB_HOST: str = "mysql-15c5d07c-gideonbasoah111-4b8.e.aivencloud.com"
    DB_PORT: int = 25339
    DB_USER: str = "avnadmin"
    DB_PASSWORD: str
    DB_NAME: str = "defaultdb"

    # SSL certificate
    DB_SSL_CA: str = "builder_wood/ca.pem"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()