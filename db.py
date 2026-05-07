"""
Conexión a Postgres para MolloAI multi-tenant
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv(override=True)

DB_HOST = os.getenv("MOLLOAI_DB_HOST", "localhost")
DB_PORT = os.getenv("MOLLOAI_DB_PORT", "5432")
DB_NAME = os.getenv("MOLLOAI_DB_NAME", "molloai")
DB_USER = os.getenv("MOLLOAI_DB_USER", "strategy_user")
DB_PASS = os.getenv("MOLLOAI_DB_PASS", "CambiaEstaPasswordFuerte123")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
