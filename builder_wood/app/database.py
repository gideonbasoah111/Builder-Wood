import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from builder_wood.app.config import settings

# ------------------------------------------------------------------
# Build the SQLAlchemy URL from Settings (env vars / .env)
# ------------------------------------------------------------------
_pw = quote_plus(settings.DB_PASSWORD or "")
SQLALCHEMY_DATABASE_URL = (
    f"mysql+mysqlconnector://{settings.DB_USER}:{_pw}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    f"?charset=utf8mb4"
)

# ------------------------------------------------------------------
# SSL: resolve the CA path relative to THIS file, not cwd.
# database.py lives at builder_wood/app/database.py, so:
#   __file__          -> .../builder_wood/app/database.py
#   .parent           -> .../builder_wood/app
#   .parent.parent    -> .../builder_wood
#   / "ca.pem"        -> .../builder_wood/ca.pem
# ------------------------------------------------------------------
_connect_args: dict = {}

if settings.DB_SSL_CA:
    _ca_path = (Path(__file__).resolve().parent.parent / "ca.pem").resolve()

    if not _ca_path.exists():
        raise FileNotFoundError(
            f"CA certificate not found at {_ca_path}. "
            f"Ensure builder_wood/ca.pem is committed and deployed."
        )

    _connect_args = {
        "ssl_ca": str(_ca_path),
        "ssl_verify_cert": True,
    }

# ------------------------------------------------------------------
# Engine / session
# ------------------------------------------------------------------
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()