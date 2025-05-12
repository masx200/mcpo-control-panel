# ================================================
# FILE: src/mcp_manager_ui/db/database.py
# ================================================
import os
from sqlmodel import create_engine, Session, SQLModel
from dotenv import load_dotenv

load_dotenv()

DATABASE_DIR = os.path.join('mcpo_manager_data')
os.makedirs(DATABASE_DIR, exist_ok=True)
DATABASE_FILE = os.path.join(DATABASE_DIR, "mcp_manager_data.db")
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

# SQLite-specific connect_args to allow session use from different threads
engine = create_engine(DATABASE_URL, echo=True, connect_args={"check_same_thread": False})

def create_db_and_tables():
    """
    Creates database file and all tables defined via SQLModel.
    Called once at application startup.
    """
    SQLModel.metadata.create_all(engine)

def get_session():
    """
    FastAPI dependency for database session management.
    Ensures proper opening and closing of the session for each request.
    """
    with Session(engine) as session:
        yield session