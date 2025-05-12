# ================================================
# FILE: src/mcp_manager_ui/api/mcpo_control.py
# (Полная версия)
# ================================================
import logging
import html # Для экранирования логов
from typing import Optional # Добавляем Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session
from fastapi.templating import Jinja2Templates

from ..db.database import get_session
from ..services import mcpo_service, config_service
from ..models.mcpo_settings import McpoSettings

# Настройка логгера
logger = logging.getLogger(__name__)

router = APIRouter()

# Переменная для шаблонов (устанавливается из main.py)
templates: Optional[Jinja2Templates] = None

# Зависимость для получения актуальных настроек mcpo
def get_mcpo_settings_dependency() -> McpoSettings:
     settings = config_service.load_mcpo_settings()
     return settings

# --- Управление процессом MCPO ---

@router.post("/start", response_class=HTMLResponse) # Убираем status_code=202, т.к. операция теперь синхронная
async def start_mcpo_process(
    request: Request,
    background_tasks: BackgroundTasks, # Оставляем на случай, если захотим вернуть фон
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """Генерирует конфиг и запускает процесс mcpo. Возвращает HTML статуса."""
    logger.info("API call: Start MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")

    # 1. Генерируем конфиг
    if not config_service.generate_mcpo_config_file(db, settings.config_file_path):
        # Возвращаем ошибку в виде HTML статуса
        error_message = "Failed to generate mcpo config file."
        logger.error(error_message)
        return templates.TemplateResponse(
            "_mcpo_status.html",
            {"request": request, "mcpo_status": "ERROR", "message": error_message},
            status_code=500
        )

    # 2. Запускаем mcpo
    success, message = await mcpo_service.start_mcpo(settings)

    # 3. Получаем актуальный статус после попытки запуска
    current_status = mcpo_service.get_mcpo_status()

    # 4. Возвращаем HTML фрагмент статуса
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )


@router.post("/stop", response_class=HTMLResponse) # Убираем status_code=202
async def stop_mcpo_process(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Останавливает процесс mcpo. Возвращает HTML статуса."""
    logger.info("API call: Stop MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")

    success, message = await mcpo_service.stop_mcpo()

    # Получаем актуальный статус после попытки остановки
    current_status = mcpo_service.get_mcpo_status()

    # Возвращаем HTML фрагмент статуса
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )

@router.post("/restart", response_class=HTMLResponse) # Убираем status_code=202, делаем синхронно
async def restart_mcpo_process(
    request: Request,
    background_tasks: BackgroundTasks, # Оставляем на случай, если захотим вернуть фон
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """Останавливает, генерирует конфиг и запускает mcpo. Возвращает HTML статуса."""
    logger.info("API call: Restart MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")

    final_message = []

    logger.info("Restart step: Stopping MCPO...")
    stop_success, stop_msg = await mcpo_service.stop_mcpo()
    logger.info(f"Restart step: Stop result: {stop_success}, {stop_msg}")
    final_message.append(f"Stop attempt: {stop_msg}")

    logger.info("Restart step: Generating config...")
    gen_success = config_service.generate_mcpo_config_file(db, settings.config_file_path)
    if not gen_success:
         error_message = "Failed to generate config file. Aborting start."
         logger.error(error_message)
         final_message.append(error_message)
         current_status = mcpo_service.get_mcpo_status() # Получаем статус после остановки
         return templates.TemplateResponse(
             "_mcpo_status.html",
             {"request": request, "mcpo_status": current_status, "message": " | ".join(final_message)},
             status_code=500
         )
    else:
        final_message.append("Config generated.")

    logger.info("Restart step: Starting MCPO...")
    start_success, start_msg = await mcpo_service.start_mcpo(settings)
    logger.info(f"Restart step: Start result: {start_success}, {start_msg}")
    final_message.append(f"Start attempt: {start_msg}")

    current_status = mcpo_service.get_mcpo_status()

    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": " | ".join(final_message)}
    )


@router.get("/status", response_class=HTMLResponse)
async def get_mcpo_process_status_html(
     request: Request,
):
    """Возвращает HTML фрагмент с текущим статусом mcpo."""
    logger.debug("API call: Get MCPO status HTML")
    if not templates: raise HTTPException(500, "Templates not configured")

    status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": status}
    )

@router.get("/logs", response_class=HTMLResponse)
async def get_mcpo_process_logs_html(
    request: Request,
    lines: int = 50,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """Возвращает HTML фрагмент с последними строками логов mcpo."""
    logger.debug(f"API call: Get MCPO logs HTML (last {lines} lines)")
    if not templates: raise HTTPException(500, "Templates not configured")

    log_lines = await mcpo_service.get_mcpo_logs(lines, settings.log_file_path)
    log_content = "\n".join(log_lines)
    escaped_logs = html.escape(log_content)
    # Оборачиваем в <pre><code> для сохранения форматирования
    return HTMLResponse(f"<pre><code>{escaped_logs}</code></pre>")


# --- Управление настройками MCPO ---
@router.get("/settings", response_model=McpoSettings)
async def get_settings(
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """Возвращает текущие настройки mcpo."""
    logger.debug("API call: GET /settings")
    return settings

@router.post("/settings", response_model=McpoSettings)
async def update_settings(
    new_settings: McpoSettings # Получаем новые настройки в теле запроса
):
    """Обновляет и сохраняет настройки mcpo."""
    logger.info("API call: POST /settings")
    if config_service.save_mcpo_settings(new_settings):
        return new_settings
    else:
        raise HTTPException(status_code=500, detail="Failed to save mcpo settings.")

# Функция для передачи шаблонов из main.py
def set_templates_for_api(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates