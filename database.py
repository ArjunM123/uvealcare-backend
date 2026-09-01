"""
Local dev uses SQLite by default so this runs with zero setup. To point
at a real hosted database, set a DATABASE_URL environment variable —
e.g. in a .env file (see .env.example) — to a real Postgres connection
string. Nothing else in the codebase changes; SQLAlchemy handles the
difference, since every query goes through the same ORM regardless of
which database is actually behind it.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base

# Loads a local .env file if one exists, so you don't have to manually
# export environment variables in every terminal session. Safe to skip
# if python-dotenv isn't installed — it just means you set env vars
# some other way (which is exactly how real hosting platforms do it).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./uvealcare.db")

if DATABASE_URL.startswith("sqlite"):
    print("Using local SQLite database. Set DATABASE_URL to use a hosted database instead.")
    connect_args = {"check_same_thread": False}
else:
    print(f"Using hosted database at {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else '(configured)'}")
    connect_args = {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
