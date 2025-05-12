# ================================================
# FILE: src/mcp_manager_ui/api/mcpo_control.py
# (Добавляем эндпоинт для получения Windows-версии конфига)
# ================================================
import logging
import html
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlmodel import Session
from fastapi.templating import Jinja2Templates

import os

from ..db.database import get_session
from ..services import mcpo_service, config_service
from ..models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None

def get_mcpo_settings_dependency() -> McpoSettings:
     return config_service.load_mcpo_settings() # Загружаем актуальные настройки

# --- Управление процессом MCPO ---
# ... ( /start, /stop, /restart, /status, /logs без изменений) ...
@router.post("/start", response_class=HTMLResponse)
async def start_mcpo_process(
    request: Request,
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    # ... (код без изменений) ...
    logger.info("API call: Start MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")
    if not config_service.generate_mcpo_config_file(db, settings): # Генерируем СТАНДАРТНЫЙ конфиг
        error_message = "Не удалось сгенерировать стандартный файл конфигурации mcpo."
        logger.error(error_message)
        return templates.TemplateResponse(
            "_mcpo_status.html",
            {"request": request, "mcpo_status": mcpo_service.get_mcpo_status(), "message": error_message},
            status_code=500
        )
    success, message = await mcpo_service.start_mcpo(settings)
    current_status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )


@router.post("/stop", response_class=HTMLResponse)
async def stop_mcpo_process(request: Request):
    # ... (код без изменений) ...
    logger.info("API call: Stop MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")
    success, message = await mcpo_service.stop_mcpo()
    current_status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )


@router.post("/restart", response_class=HTMLResponse)
async def restart_mcpo_process(
    request: Request,
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    # ... (код restart_mcpo_process_with_new_config в mcpo_service должен вызывать стандартный generate_mcpo_config_file) ...
    logger.info("API call: Restart MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")
    success, message = await mcpo_service.restart_mcpo_process_with_new_config(db, settings)
    current_status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )

@router.get("/status", response_class=HTMLResponse)
async def get_mcpo_process_status_html(request: Request):
    # ... (код без изменений) ...
    logger.debug("API call: Get MCPO status HTML")
    if not templates: raise HTTPException(500, "Templates not configured")
    status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": status}
    )

@router.get("/logs", response_class=HTMLResponse, name="api_get_logs_html_content") # Имя изменено для устранения конфликта
async def get_mcpo_process_logs_html(
    request: Request,
    lines: int = 100,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    logger.debug(f"API call: Get MCPO logs HTML (last {lines} lines)")
    if not templates: raise HTTPException(500, "Templates not configured")
    if not settings.log_file_path:
        # ВАЖНО: Возвращаем HTML внутри <pre><code> для корректной вставки
        return HTMLResponse("<pre><code>Путь к файлу логов не настроен.</code></pre>")
    if not os.path.exists(settings.log_file_path):
         # ВАЖНО: Возвращаем HTML внутри <pre><code>
        return HTMLResponse(f"<pre><code>Файл логов не найден: {html.escape(settings.log_file_path)}</code></pre>")

    log_lines = await mcpo_service.get_mcpo_logs(lines, settings.log_file_path)
    log_content = "\n".join(log_lines)
    escaped_logs = html.escape(log_content)
    # ВАЖНО: Оборачиваем результат в <pre><code>
    return HTMLResponse(f"<pre><code>{escaped_logs}</code></pre>")

@router.get("/logs/content", response_class=HTMLResponse, name="api_get_logs_content_html")
async def get_mcpo_process_logs_html_fragment(
    lines: int = 200, # По умолчанию 200 строк, как в шаблоне
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """
    Возвращает HTML-фрагмент с последними строками логов (экранированный HTML).
    Предназначен для использования с HTMX (вставляется в <code>).
    """
    logger.debug(f"API call (HTMX): Get MCPO logs HTML fragment (last {lines} lines)")

    if not settings.log_file_path:
        logger.warning("API call (HTMX): Log file path not configured.")
        return HTMLResponse("Путь к файлу логов не настроен.")

    if not os.path.exists(settings.log_file_path):
        logger.warning(f"API call (HTMX): Log file not found at '{settings.log_file_path}'.")
        return HTMLResponse(f"Файл логов не найден: {html.escape(settings.log_file_path)}")

    try:
        log_lines = await mcpo_service.get_mcpo_logs(lines, settings.log_file_path)
        # Обработка случая, когда get_mcpo_logs возвращает строки с ошибками
        if log_lines and log_lines[0].startswith("Ошибка:"):
             log_content = "\n".join(log_lines) # Объединяем ошибки, если их несколько
             escaped_logs = html.escape(log_content)
        elif log_lines:
             log_content = "\n".join(log_lines)
             escaped_logs = html.escape(log_content).replace('\n', '<br>') # Экранируем и заменяем переносы
        else:
             escaped_logs = "Лог-файл пуст."

        # Возвращаем только контент для <code>
        return HTMLResponse(content=escaped_logs)
    except Exception as e:
        logger.error(f"API call (HTMX): Error reading log file '{settings.log_file_path}': {e}", exc_info=True)
        return HTMLResponse(f"Ошибка чтения файла логов: {html.escape(str(e))}")

# --- Получение сгенерированного конфига ---
@router.get("/generated-config", response_class=PlainTextResponse)
async def get_generated_mcpo_config_content(
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """
    Возвращает содержимое **стандартного** сгенерированного файла конфигурации MCPO.
    """
    logger.debug("API call: Get standard generated MCPO config content")
    config_path = settings.config_file_path
    error_prefix = "Ошибка получения стандартного конфига: "

    if not config_path:
        logger.warning(f"{error_prefix}Путь к файлу конфигурации не задан в настройках.")
        return PlainTextResponse(content=f"{error_prefix}Путь к файлу конфигурации не задан.", status_code=404)

    if not os.path.exists(config_path):
        logger.warning(f"{error_prefix}Файл '{config_path}' не найден.")
        return PlainTextResponse(content=f"{error_prefix}Файл '{config_path}' не найден.", status_code=404)

    try:
        with open(config_path, 'r', encoding='utf-8') as f: content = f.read()
        return PlainTextResponse(content=content, media_type="application/json")
    except Exception as e:
        logger.error(f"Ошибка чтения стандартного файла конфигурации '{config_path}': {e}", exc_info=True)
        return PlainTextResponse(content=f"{error_prefix}Ошибка чтения файла '{config_path}'.", status_code=500)

# --- НОВЫЙ ЭНДПОИНТ: Получение конфига для Windows ---
@router.get("/generated-config-windows", response_class=PlainTextResponse)
async def get_generated_mcpo_config_content_windows(
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """
    Генерирует и возвращает содержимое конфигурации MCPO, адаптированное для Windows.
    """
    logger.debug("API call: Get Windows-adapted generated MCPO config content")
    try:
        # Вызываем новую функцию из config_service
        windows_config_content = config_service.generate_mcpo_config_content_for_windows(db, settings)
        # Проверяем, не вернулась ли строка с ошибкой
        if windows_config_content.startswith("// Ошибка генерации конфига для Windows:"):
            logger.error(f"Ошибка при генерации Windows-конфига: {windows_config_content}")
            return PlainTextResponse(content=windows_config_content, status_code=500)
        else:
             return PlainTextResponse(content=windows_config_content, media_type="application/json")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при получении Windows-конфига: {e}", exc_info=True)
        return PlainTextResponse(content=f"// Непредвиденная ошибка сервера при генерации Windows-конфига.", status_code=500)


# --- Управление настройками MCPO (без изменений) ---
@router.get("/settings", response_model=McpoSettings)
async def get_settings(settings: McpoSettings = Depends(get_mcpo_settings_dependency)):
    # ... (код без изменений) ...
    logger.debug("API call: GET /settings")
    return settings

@router.post("/settings", response_model=McpoSettings)
async def update_settings(new_settings_payload: McpoSettings):
    # ... (код без изменений) ...
    logger.info("API call: POST /settings (Обновление всех настроек)")
    if config_service.save_mcpo_settings(new_settings_payload):
        return new_settings_payload
    else:
        raise HTTPException(status_code=500, detail="Не удалось сохранить настройки mcpo.")

# Функция для передачи шаблонов из main.py
def set_templates_for_api(jinja_templates: Jinja2Templates):
    # ... (код без изменений) ...
    global templates
    templates = jinja_templates