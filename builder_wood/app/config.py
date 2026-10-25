from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DEBUG: bool = True
    APP_NAME: str = "Builder Wood"
    SECRET_KEY: str = "super-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # --- PAYSTACK SETTINGS ---
    # REPLACE THIS with your NEW regenerated test secret key
    PAYSTACK_SECRET_KEY: str = "sk_test_7a9e40d9c1374561f3659ee5820dc0e9180f6a2c"

    # Localhost URL (where Paystack will redirect the buyer after payment)
    BASE_URL: str = "http://127.0.0.1:8000"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()