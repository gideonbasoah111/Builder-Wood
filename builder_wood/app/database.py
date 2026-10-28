import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from builder_wood.app.config import settings  # adjust import to your actual path

_pw = quote_plus(settings.DB_PASSWORD or "")
SQLALCHEMY_DATABASE_URL = (
    f"mysql+mysqlconnector://{settings.DB_USER}:{_pw}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    f"?charset=utf8mb4"
)

# Resolve the CA path relative to the project root
_ca_path = settings.DB_SSL_CA
if not os.path.isabs(_ca_path):
    _ca_path = os.path.join(os.getcwd(), _ca_path)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "ssl_ca": _ca_path,
        "ssl_verify_cert": True,
    },
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