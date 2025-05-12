# ================================================
# FILE: mcpo_control_panel/main.py
# ================================================
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
from pathlib import Path # <--- Добавлено
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

# Определяем базовую директорию приложения (где находится main.py)
APP_BASE_DIR = Path(__file__).resolve().parent

# Формируем абсолютные пути к директориям static и templates
static_dir_path = APP_BASE_DIR / "ui" / "static"
templates_dir_path = APP_BASE_DIR / "ui" / "templates"

# Преобразуем Path объекты в строки для использования в os.makedirs и конфигурациях
static_dir_str = str(static_dir_path)
templates_dir_str = str(templates_dir_path)

# Создаем директории, если они не существуют, используя абсолютные пути
os.makedirs(static_dir_path / "css", exist_ok=True)
os.makedirs(static_dir_path / "js", exist_ok=True)
os.makedirs(templates_dir_path, exist_ok=True)
logger.info(f"Ensured static subdirectories exist in: {static_dir_path}")
logger.info(f"Ensured templates directory exists: {templates_dir_path}")

try:
    app.mount("/static", StaticFiles(directory=static_dir_str), name="static")
    logger.info(f"Static files mounted from directory: '{static_dir_str}'")
except RuntimeError as e:
     logger.error(f"Error mounting static files from '{static_dir_str}': {e}.")

templates = Jinja2Templates(directory=templates_dir_str)
import datetime
templates.env.globals['now'] = datetime.datetime.utcnow

ui_router.templates = templates
server_api_router.set_templates_for_api(templates)
mcpo_api_router.set_templates_for_api(templates)
logger.info(f"Jinja2 templates configured for directory '{templates_dir_str}'")

app.include_router(mcpo_api_router.router, prefix="/api/mcpo", tags=["MCPO Control API"])
app.include_router(server_api_router.router, prefix="/api/servers", tags=["Server Definition API"])
logger.info("UI and API routers connected.")

from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
async def read_root_redirect():
    return RedirectResponse(url="/ui")

app.include_router(ui_router.router, prefix="/ui", include_in_schema=False)