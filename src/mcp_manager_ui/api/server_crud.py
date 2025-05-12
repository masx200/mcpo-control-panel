# ================================================
# FILE: src/mcp_manager_ui/api/server_crud.py
# (Полная версия)
# ================================================
import logging
from typing import Optional # Добавляем Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session
from fastapi.templating import Jinja2Templates

from ..db.database import get_session
from ..services import config_service
from ..models.server_definition import ServerDefinitionRead

# Настройка логгера
logger = logging.getLogger(__name__)

router = APIRouter()

# Переменная для шаблонов (устанавливается из main.py)
templates: Optional[Jinja2Templates] = None

# --- API для HTMX взаимодействия с определениями серверов ---

@router.post("/{server_id}/toggle", response_class=HTMLResponse)
async def toggle_server(
    request: Request,
    server_id: int,
    db: Session = Depends(get_session)
):
    """
    Переключает флаг is_enabled для сервера и возвращает обновленную строку таблицы (HTML).
    """
    logger.info(f"API Request: POST /api/servers/{server_id}/toggle")
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for API router")

    updated_definition = config_service.toggle_server_enabled(db, server_id)
    if not updated_definition:
        raise HTTPException(status_code=404, detail="Server definition not found")

    definition_read = ServerDefinitionRead.model_validate(updated_definition)

    return templates.TemplateResponse(
        "_server_row.html",
        {"request": request, "server": definition_read}
    )

@router.delete("/{server_id}", status_code=200)
async def delete_server(
    server_id: int,
    db: Session = Depends(get_session)
):
    """
    Удаляет определение сервера. Возвращает пустой ответ.
    """
    logger.info(f"API Request: DELETE /api/servers/{server_id}")
    deleted = config_service.delete_server_definition(db, server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Server definition not found")

    # Важно: Возвращаем пустой ответ, чтобы HTMX удалил элемент со страницы
    return Response(status_code=200)

# Функция для передачи шаблонов из main.py
def set_templates_for_api(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates