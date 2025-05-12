# ================================================
# FILE: src/mcp_manager_ui/main.py
# (Обновляем существующий файл)
# ================================================
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os # Добавляем os

from .db.database import create_db_and_tables
from .ui import routes as ui_router
from .api import mcpo_control as mcpo_api_router
from .api import server_crud as server_api_router # Импортируем API CRUD серверов

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MCP Manager UI...")
    create_db_and_tables()
    logger.info("Database tables checked/created.")
    yield
    logger.info("Shutting down MCP Manager UI...")

# Создаем экземпляр FastAPI с lifespan
app = FastAPI(title="MCP Manager UI", lifespan=lifespan)

# --- Настройка статики и шаблонов ---
static_dir = "src/mcp_manager_ui/ui/static"
templates_dir = "src/mcp_manager_ui/ui/templates"

# Создаем папки, если их нет (для удобства первого запуска)
os.makedirs(os.path.join(static_dir, "css"), exist_ok=True)
os.makedirs(os.path.join(static_dir, "js"), exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)
# TODO: Добавить сюда создание пустых файлов CSS/JS и базового шаблона, если их нет?

try:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Static files mounted from '{static_dir}'")
except RuntimeError as e:
     logger.error(f"Error mounting static files from '{static_dir}': {e}. Make sure the directory exists.")

templates = Jinja2Templates(directory=templates_dir)
# Добавляем функцию now() в глобальные переменные Jinja, чтобы использовать в base.html
import datetime
templates.env.globals['now'] = datetime.datetime.utcnow

# Передаем templates в модули роутов, которым они нужны
ui_router.templates = templates
# Передаем templates в API CRUD, т.к. он рендерит _server_row.html
server_api_router.set_templates_for_api(templates)
# Передаем templates в API управления mcpo, т.к. он рендерит _mcpo_status.html
mcpo_api_router.set_templates_for_api(templates)

logger.info(f"Jinja2 templates configured for directory '{templates_dir}'")

# --- Подключаем роутеры ---
app.include_router(ui_router.router, tags=["UI"])
app.include_router(mcpo_api_router.router, prefix="/api/mcpo", tags=["MCPO Control API"])
app.include_router(server_api_router.router, prefix="/api/servers", tags=["Server Definition API"])

logger.info("UI and API routers included.")

# Заменяем базовый роут на редирект к UI
from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
async def read_root_redirect():
    return RedirectResponse(url="/ui") # Перенаправляем на UI

# Добавляем роут /ui как алиас для /
app.include_router(ui_router.router, prefix="/ui", include_in_schema=False)