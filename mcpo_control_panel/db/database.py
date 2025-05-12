# ================================================
# FILE: mcpo_control_panel/db/database.py
# ================================================
import os
from pathlib import Path # Added Path
from sqlmodel import create_engine, Session, SQLModel
from dotenv import load_dotenv
import logging
load_dotenv()

# Determine the database directory using MCPO_MANAGER_DATA_DIR_EFFECTIVE
# Default to Path.home() / ".mcpo_manager_data" if the env var is not set
# This aligns with how config_service determines its data directory.
DEFAULT_DATA_DIR_NAME = ".mcpo_manager_data"
DATABASE_FILENAME = "mcp_manager_data.db"

def get_database_url() -> str:
    """Determines the database URL based on the effective data directory."""
    logger = logging.getLogger(__name__) # Ensure logger is available

    effective_data_dir_str = os.getenv("MCPO_MANAGER_DATA_DIR_EFFECTIVE")
    logger.info(f"Retrieved MCPO_MANAGER_DATA_DIR_EFFECTIVE: '{effective_data_dir_str}'")

    if not effective_data_dir_str:
        # This case should ideally not be reached if __main__.py works as expected
        logger.error("MCPO_MANAGER_DATA_DIR_EFFECTIVE is not set! Falling back to a default, but this indicates an issue.")
        # Fallback to a default similar to __main__.py's default logic for robustness, though it's a symptom of a problem.
        database_dir = Path.home() / DEFAULT_DATA_DIR_NAME
    else:
        database_dir = Path(effective_data_dir_str)
    
    logger.info(f"Raw database directory path: '{database_dir}'")
    resolved_database_dir = database_dir.resolve()
    logger.info(f"Resolved database directory: '{resolved_database_dir}'")
    
    try:
        logger.info(f"Attempting to create directory (if not exists): {resolved_database_dir}")
        resolved_database_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Directory ensured: {resolved_database_dir}")
    except Exception as e:
        logger.error(f"Failed to create database directory {resolved_database_dir}: {e}", exc_info=True)
        # If directory creation fails, subsequent operations will likely fail.
        # Consider re-raising or handling appropriately. For now, log and continue.

    database_file_path = resolved_database_dir / DATABASE_FILENAME
    resolved_db_file_path = database_file_path.resolve() # Resolve final file path

    logger.info(f"Database file path set to: {resolved_db_file_path}")
    
    db_url = f"sqlite:///{resolved_db_file_path}"
    logger.info(f"Final Database URL: {db_url}")
    return db_url

DATABASE_URL = get_database_url()

# SQLite-specific connect_args to allow session use from different threads
# The engine should be created with the dynamically determined DATABASE_URL
engine = create_engine(DATABASE_URL, echo=True, connect_args={"check_same_thread": False})

def create_db_and_tables():
    """
    Creates database file and all tables defined via SQLModel.
    Called once at application startup.
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Attempting to create database and tables using DATABASE_URL: {DATABASE_URL}")
    # Extract file path from DATABASE_URL to check existence
    db_file_path_from_url_str = DATABASE_URL.split("sqlite:///")[-1]
    db_file_path_obj = Path(db_file_path_from_url_str)
    
    logger.info(f"Expected database file location: {db_file_path_obj}")

    if db_file_path_obj.exists():
        logger.info(f"Database file already exists at: {db_file_path_obj}")
    else:
        logger.info(f"Database file does not exist at: {db_file_path_obj}. Expecting create_all to create it.")

    try:
        SQLModel.metadata.create_all(engine)
        logger.info("SQLModel.metadata.create_all(engine) executed.")
        
        # Verify file existence again after create_all
        if db_file_path_obj.exists():
            logger.info(f"SUCCESS: Database file confirmed to exist at: {db_file_path_obj} after create_all.")
        else:
            logger.error(f"FAILURE: Database file STILL DOES NOT exist at: {db_file_path_obj} after create_all call!")
            # Log additional info about the directory
            if db_file_path_obj.parent.exists():
                logger.info(f"Parent directory {db_file_path_obj.parent} exists.")
                try:
                    # Attempt to list contents of parent directory for diagnostics
                    content = list(db_file_path_obj.parent.iterdir())
                    logger.info(f"Contents of {db_file_path_obj.parent}: {content}")
                except Exception as e_dir:
                    logger.error(f"Could not list contents of {db_file_path_obj.parent}: {e_dir}")
            else:
                logger.error(f"Parent directory {db_file_path_obj.parent} does NOT exist.")

    except Exception as e:
        logger.error(f"Error during SQLModel.metadata.create_all(engine): {e}", exc_info=True)
        # Depending on the application's needs, you might want to re-raise the exception
        # or handle it in a way that prevents the application from starting if the DB is critical.
        # For now, just logging the error.
        # raise # Uncomment to make the application fail hard on DB creation error

def get_session():
    """
    FastAPI dependency for database session management.
    Ensures proper opening and closing of the session for each request.
    """
    with Session(engine) as session:
        yield session