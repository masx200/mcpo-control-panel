# ================================================
# FILE: mcpo_control_panel/main.py
# (Updated lifespan to start/stop MCPO)
# ================================================
import logging
from contextlib import asynccontextmanager, AbstractAsyncContextManager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
from pathlib import Path
import asyncio
from typing import Optional, AsyncGenerator # Added AsyncGenerator
from sqlmodel import Session # Import Session for type hint

from .db.database import create_db_and_tables, get_session, engine # Import engine for session creation
from .ui import routes as ui_router
from .api import mcpo_control as mcpo_api_router
from .api import server_crud as server_api_router
from .services import mcpo_service, config_service # Import config_service

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Variable to store the Health Check background task
health_check_task: Optional[asyncio.Task] = None

# Helper context manager to get a session within lifespan startup
@asynccontextmanager
async def lifespan_get_session() -> AsyncGenerator[Session, None]:
    """Provides a session scope specifically for the lifespan startup actions."""
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global health_check_task
    logger.info("Starting MCP Manager UI lifespan...")
    create_db_and_tables()
    logger.info("Database tables checked/created.")

    # -- Startup Actions --
    mcpo_started = False
    try:
        async with lifespan_get_session() as db_session: # Get session for startup tasks
             settings = config_service.load_mcpo_settings()
             logger.info("Settings loaded for startup.")

             # Generate initial config file
             if config_service.generate_mcpo_config_file(db_session, settings):
                 logger.info("Initial MCPO configuration file generated.")
             else:
                 logger.error("Failed to generate initial MCPO configuration file during startup.")
                 # Decide if we should proceed without config? For now, log and continue.

             # Attempt to start the MCPO process
             logger.info("Attempting to start MCPO process on application startup...")
             start_success, start_message = await mcpo_service.start_mcpo(settings)
             if start_success:
                 logger.info(f"MCPO process started successfully via lifespan: {start_message}")
                 mcpo_started = True
             else:
                 logger.error(f"Failed to start MCPO process during lifespan startup: {start_message}")
                 # Application will continue, but MCPO won't be running initially

        # Start the Health Check background task *after* attempting to start MCPO
        # Pass the original get_session dependency function
        health_check_task = asyncio.create_task(mcpo_service.run_health_check_loop_async(get_session))
        logger.info("Health Check background task for MCPO started.")

    except Exception as startup_e:
         logger.error(f"Error during MCP Manager startup sequence: {startup_e}", exc_info=True)
         # Stop MCPO if it managed to start before the error
         if mcpo_started:
             logger.warning("Stopping MCPO due to error during later startup phase...")
             await mcpo_service.stop_mcpo() # Attempt cleanup
         # Allow app to potentially continue, but log the critical failure
         # Depending on the error, FastAPI might terminate anyway.

    # -- Application Runs --
    yield # Application runs here

    # -- Shutdown Actions --
    logger.info("Initiating MCP Manager shutdown sequence...")

    # Stop Health Check Task
    if health_check_task and not health_check_task.done():
        logger.info("Stopping Health Check background task...")
        health_check_task.cancel()
        try:
            await health_check_task
        except asyncio.CancelledError:
            logger.info("Health Check background task successfully cancelled.")
        except Exception as e:
            logger.error(f"Error waiting for Health Check background task termination: {e}", exc_info=True)

    # Stop MCPO server if it's managed by the service
    logger.info("Attempting to stop MCPO server during application shutdown...")
    try:
        stop_success, stop_message = await mcpo_service.stop_mcpo()
        if stop_success:
            logger.info(f"MCPO server stop attempt result: {stop_message}")
        else:
            logger.warning(f"MCPO server stop attempt during shutdown returned: {stop_message}")
    except Exception as e:
        logger.error(f"Error during MCPO server stop on shutdown: {e}", exc_info=True)

    logger.info("MCP Manager UI lifespan finished.")

app = FastAPI(title="MCP Manager UI", lifespan=lifespan)

# Determine application base directory
APP_BASE_DIR = Path(__file__).resolve().parent

# Construct absolute paths for static and templates
static_dir_path = APP_BASE_DIR / "ui" / "static"
templates_dir_path = APP_BASE_DIR / "ui" / "templates"

# Ensure directories exist
try:
    static_dir_path.mkdir(parents=True, exist_ok=True)
    (static_dir_path / "css").mkdir(exist_ok=True)
    (static_dir_path / "js").mkdir(exist_ok=True)
    templates_dir_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured static directory structure exists in: {static_dir_path}")
    logger.info(f"Ensured templates directory exists: {templates_dir_path}")
except Exception as e:
     logger.error(f"Error creating static/template directories: {e}", exc_info=True)

# Mount static files
try:
    app.mount("/static", StaticFiles(directory=str(static_dir_path)), name="static")
    logger.info(f"Static files mounted from directory: '{static_dir_path}'")
except RuntimeError as e:
     logger.error(f"Error mounting static files from '{static_dir_path}': {e}.")

# Configure Jinja2 templates
templates = Jinja2Templates(directory=str(templates_dir_path))
import datetime
templates.env.globals['now'] = datetime.datetime.utcnow # Add utcnow to globals

# Set templates for routers that need them
ui_router.templates = templates
server_api_router.set_templates_for_api(templates)
mcpo_api_router.set_templates_for_api(templates)
logger.info(f"Jinja2 templates configured for directory '{templates_dir_path}'")

# Include API routers
app.include_router(mcpo_api_router.router, prefix="/api/mcpo", tags=["MCPO Control API"])
app.include_router(server_api_router.router, prefix="/api/servers", tags=["Server Definition API"])
logger.info("API routers included.")

# Include UI router and root redirect
from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
async def read_root_redirect():
    return RedirectResponse(url="/ui")

app.include_router(ui_router.router, prefix="/ui", include_in_schema=False)
logger.info("UI router included.")