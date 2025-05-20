# ================================================
# FILE: mcpo_control_panel/main.py
# (Lifespan updated for MCPO start/stop, UI router refactored)
# ================================================
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import os
from pathlib import Path
import asyncio
from typing import Optional, AsyncGenerator
from sqlmodel import Session

from .db.database import create_db_and_tables, get_session, engine
from .ui import routes as ui_router_module  # Imports the aggregator from mcpo_control_panel/ui/routes.py
from .api import mcpo_control as mcpo_api_router
from .api import server_crud as server_api_router
from .services import mcpo_service, config_service # config_service is now the facade

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

health_check_task: Optional[asyncio.Task] = None

# Load settings globally for app configuration
mcpo_global_settings = config_service.load_mcpo_settings()

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

    mcpo_started = False
    try:
        async with lifespan_get_session() as db_session: # Get session for startup tasks
             settings = config_service.load_mcpo_settings()
             logger.info("Settings loaded for startup.")

             # Generate/ensure initial config file.
             # config_service.generate_mcpo_config_file now handles manual_config_mode_enabled:
             # - If manual mode is on and file exists, it's not touched.
             # - If manual mode is on and file doesn't exist, an empty default is created.
             # - If manual mode is off, it's generated from DB.
             if config_service.generate_mcpo_config_file(db_session, settings):
                 logger.info("Initial MCPO configuration file ensured (generated or checked for manual mode).")
             else:
                 # This else branch might be less likely to hit if the default empty creation is robust.
                 logger.error("Failed to ensure initial MCPO configuration file during startup.")
                 # Decide if we should proceed. For now, log and continue.

             # Attempt to start the MCPO process
             logger.info("Attempting to start MCPO process on application startup...")
             start_success, start_message = await mcpo_service.start_mcpo(settings)
             if start_success:
                 logger.info(f"MCPO process started successfully via lifespan: {start_message}")
                 mcpo_started = True
             else:
                 logger.error(f"Failed to start MCPO process during lifespan startup: {start_message}")

        # Start the Health Check background task *after* attempting to start MCPO
        health_check_task = asyncio.create_task(mcpo_service.run_health_check_loop_async(get_session))
        logger.info("Health Check background task for MCPO started.")

    except Exception as startup_e:
         logger.error(f"Error during MCP Manager startup sequence: {startup_e}", exc_info=True)
         if mcpo_started:
             logger.warning("Stopping MCPO due to error during later startup phase...")
             await mcpo_service.stop_mcpo() # Attempt cleanup
    
    # Application Runs
    yield 

    # Shutdown Actions
    logger.info("Initiating MCP Manager shutdown sequence...")

    if health_check_task and not health_check_task.done():
        logger.info("Stopping Health Check background task...")
        health_check_task.cancel()
        try:
            await health_check_task
        except asyncio.CancelledError:
            logger.info("Health Check background task successfully cancelled.")
        except Exception as e:
            logger.error(f"Error waiting for Health Check background task termination: {e}", exc_info=True)

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

app = FastAPI(
    title="MCP Manager UI",
    lifespan=lifespan,
    root_path=mcpo_global_settings.root_path if mcpo_global_settings else ""
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

APP_BASE_DIR = Path(__file__).resolve().parent
static_dir_path = APP_BASE_DIR / "ui" / "static"
templates_dir_path = APP_BASE_DIR / "ui" / "templates"

try:
    static_dir_path.mkdir(parents=True, exist_ok=True)
    (static_dir_path / "css").mkdir(exist_ok=True)
    (static_dir_path / "js").mkdir(exist_ok=True)
    templates_dir_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured static directory structure exists in: {static_dir_path}")
    logger.info(f"Ensured templates directory exists: {templates_dir_path}")
except Exception as e:
     logger.error(f"Error creating static/template directories: {e}", exc_info=True)

try:
    app.mount("/static", StaticFiles(directory=str(static_dir_path)), name="static")
    logger.info(f"Static files mounted from directory: '{static_dir_path}'")
except RuntimeError as e:
     logger.error(f"Error mounting static files from '{static_dir_path}': {e}.")

templates = Jinja2Templates(directory=str(templates_dir_path))
import datetime
templates.env.globals['now'] = datetime.datetime.utcnow

# Pass templates to routers
ui_router_module.set_templates_for_ui_routers(templates) # For the aggregated UI router
server_api_router.set_templates_for_api(templates) # For server_crud API if it uses templates
mcpo_api_router.set_templates_for_api(templates) # For mcpo_control API if it uses templates
logger.info(f"Jinja2 templates configured for directory '{templates_dir_path}' and passed to relevant routers.")

# Include API routers
app.include_router(mcpo_api_router.router, prefix="/api/mcpo", tags=["MCPO Control API"])
app.include_router(server_api_router.router, prefix="/api/servers", tags=["Server Definition API"])
logger.info("API routers included.")

# Include UI router and root redirect
from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
async def read_root_redirect():
    return RedirectResponse(url="/ui")

# Include the main UI router aggregator, now correctly imported
app.include_router(ui_router_module.router, prefix="/ui", include_in_schema=False)
logger.info("UI router aggregator included.")