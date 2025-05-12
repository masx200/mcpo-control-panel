# ================================================
# FILE: mcpo_control_panel/main.py
# ================================================
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import asyncio
from typing import Optional
from .db.database import create_db_and_tables, get_session
from .ui import routes as ui_router
from .api import mcpo_control as mcpo_api_router
from .api import server_crud as server_api_router
from .services import mcpo_service

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Variable to store the Health Check background task
health_check_task: Optional[asyncio.Task] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global health_check_task
    logger.info("Starting MCP Manager UI...")
    create_db_and_tables()
    logger.info("Database tables checked/created.")

    # Start the Health Check background task
    # Pass get_session as a function that health_check_loop can call to get a session
    # This is more flexible than passing the engine directly
    health_check_task = asyncio.create_task(mcpo_service.run_health_check_loop_async(get_session))
    logger.info("Health Check background task for MCPO started.")

    yield # Application runs here

    # Properly terminate the background task when the application stops
    if health_check_task:
        logger.info("Stopping Health Check background task...")
        health_check_task.cancel()
        try:
            await health_check_task
        except asyncio.CancelledError:
            logger.info("Health Check background task successfully cancelled.")
        except Exception as e:
            logger.error(f"Error terminating Health Check background task: {e}", exc_info=True)

    # Stop MCPO server if it's running
    logger.info("Attempting to stop MCPO server if running...")
    try:
        stop_success, stop_message = await mcpo_service.stop_mcpo()
        if stop_success:
            logger.info(f"MCPO server stop attempt result: {stop_message}")
        else:
            logger.warning(f"MCPO server stop attempt failed: {stop_message}")
    except Exception as e:
        logger.error(f"Error during MCPO server stop: {e}", exc_info=True)

    logger.info("MCP Manager UI stopped.")

app = FastAPI(title="MCP Manager UI", lifespan=lifespan)

static_dir = "mcpo_control_panel/ui/static"
templates_dir = "mcpo_control_panel/ui/templates"
os.makedirs(os.path.join(static_dir, "css"), exist_ok=True)
os.makedirs(os.path.join(static_dir, "js"), exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

try:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
except RuntimeError as e:
     logger.error(f"Error mounting static files from '{static_dir}': {e}.")

templates = Jinja2Templates(directory=templates_dir)
import datetime
templates.env.globals['now'] = datetime.datetime.utcnow

ui_router.templates = templates
server_api_router.set_templates_for_api(templates)
mcpo_api_router.set_templates_for_api(templates)
logger.info(f"Jinja2 templates configured for directory '{templates_dir}'")

app.include_router(mcpo_api_router.router, prefix="/api/mcpo", tags=["MCPO Control API"])
app.include_router(server_api_router.router, prefix="/api/servers", tags=["Server Definition API"])
logger.info("UI and API routers connected.")

from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
async def read_root_redirect():
    return RedirectResponse(url="/ui")

app.include_router(ui_router.router, prefix="/ui", include_in_schema=False)