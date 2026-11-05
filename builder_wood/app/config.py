from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DEBUG: bool = False
    APP_NAME: str = "Builder Wood"

    # Application security (REQUIRED)
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # Paystack (REQUIRED)
    PAYSTACK_SECRET_KEY: str

    # Application URL
    BASE_URL: str = "https://builder-wood-2.onrender.com"

    # Aiven MySQL database
    DB_HOST: str = "mysql-15c5d07c-gideonbasoah111-4b8.e.aivencloud.com"
    DB_PORT: int = 25339
    DB_USER: str = "avnadmin"
    DB_PASSWORD: str
    DB_NAME: str = "defaultdb"

    # SSL certificate
    DB_SSL_CA: str = "builder_wood/ca.pem"

    # Cloudinary image storage
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Delivery distance (Nominatim + OSRM)
    NOMINATIM_USER_AGENT: str = (
        "BuilderWood/1.0 (https://builder-wood-2.onrender.com; "
        "contact@builderwood.gh)"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()