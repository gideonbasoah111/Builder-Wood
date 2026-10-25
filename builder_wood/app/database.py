from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# XAMPP default: user=root, no password, port 3306
SQLALCHEMY_DATABASE_URL = (
    "mysql+mysqlconnector://root:new_secure_password@localhost:3306/woodshop_db"
    "?charset=utf8mb4"
)


engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,     # reconnects if XAMPP restarted
    pool_recycle=3600,      # MySQL drops idle connections after 8h; refresh hourly
    echo=False,             # set True to see SQL while debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()