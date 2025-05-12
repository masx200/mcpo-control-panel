# ================================================
# FILE: src/mcp_manager_ui/main.py
# (Запускаем фоновую задачу Health Check)
# ================================================
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import asyncio # Для фоновых задач
from typing import Optional
from .db.database import create_db_and_tables, get_session # get_session может понадобиться для передачи
from .ui import routes as ui_router
from .api import mcpo_control as mcpo_api_router
from .api import server_crud as server_api_router
from .services import mcpo_service # Для запуска health check

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Переменная для хранения фоновой задачи Health Check ---
health_check_task: Optional[asyncio.Task] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global health_check_task
    logger.info("Запуск MCP Manager UI...")
    create_db_and_tables()
    logger.info("Таблицы базы данных проверены/созданы.")

    # Запускаем фоновую задачу Health Check
    # Передаем get_session как функцию, которую health_check_loop сможет вызвать для получения сессии
    # Это более гибко, чем передавать engine напрямую.
    health_check_task = asyncio.create_task(mcpo_service.run_health_check_loop_async(get_session))
    logger.info("Фоновая задача Health Check для MCPO запущена.")

    yield # Приложение работает здесь

    # Корректное завершение фоновой задачи при остановке приложения
    if health_check_task:
        logger.info("Остановка фоновой задачи Health Check...")
        health_check_task.cancel()
        try:
            await health_check_task
        except asyncio.CancelledError:
            logger.info("Фоновая задача Health Check успешно отменена.")
        except Exception as e:
            logger.error(f"Ошибка при завершении фоновой задачи Health Check: {e}", exc_info=True)
    logger.info("MCP Manager UI остановлен.")

app = FastAPI(title="MCP Manager UI", lifespan=lifespan)

static_dir = "src/mcp_manager_ui/ui/static"
templates_dir = "src/mcp_manager_ui/ui/templates"
os.makedirs(os.path.join(static_dir, "css"), exist_ok=True)
os.makedirs(os.path.join(static_dir, "js"), exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

try:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
except RuntimeError as e:
     logger.error(f"Ошибка монтирования статических файлов из '{static_dir}': {e}.")

templates = Jinja2Templates(directory=templates_dir)
import datetime
templates.env.globals['now'] = datetime.datetime.utcnow

ui_router.templates = templates
server_api_router.set_templates_for_api(templates)
mcpo_api_router.set_templates_for_api(templates)
logger.info(f"Шаблоны Jinja2 настроены для директории '{templates_dir}'")

app.include_router(mcpo_api_router.router, prefix="/api/mcpo", tags=["MCPO Control API"])
app.include_router(server_api_router.router, prefix="/api/servers", tags=["Server Definition API"])
logger.info("UI и API роутеры подключены.")

from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
async def read_root_redirect():
    return RedirectResponse(url="/ui")

app.include_router(ui_router.router, prefix="/ui", include_in_schema=False)