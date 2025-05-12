# ================================================
# FILE: src/mcp_manager_ui/ui/routes.py
# (Добавляем роут /logs, убираем логи из index, обновляем настройки)
# ================================================
import logging
# shlex больше не нужен, так как args приходят списком
import json
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session
from pydantic import ValidationError

from ..db.database import get_session
from ..services import config_service, mcpo_service
from ..models.server_definition import ServerDefinitionCreate, ServerDefinitionUpdate, ServerDefinitionRead
from ..models.mcpo_settings import McpoSettings # Импортируем модель настроек

import os

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None

# --- Основные страницы UI ---
@router.get("/", response_class=HTMLResponse, name="ui_root")
async def get_index_page(
    request: Request,
    db: Session = Depends(get_session)
):
    """Отображает главную страницу со списком серверов и управлением mcpo."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info("UI Request: GET / (Главная страница)")
    try:
        server_definitions = config_service.get_server_definitions(db)
        definitions_read = [ServerDefinitionRead.model_validate(d) for d in server_definitions]
        current_mcpo_status = mcpo_service.get_mcpo_status()
        mcpo_settings = config_service.load_mcpo_settings()
    except Exception as e:
        logger.error(f"Ошибка загрузки данных для главной страницы: {e}", exc_info=True)
        definitions_read = []
        current_mcpo_status = "ERROR"
        mcpo_settings = McpoSettings() # Дефолтные

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "server_definitions": definitions_read,
            "mcpo_status": current_mcpo_status, # Передаем статус mcpo
            "mcpo_settings": mcpo_settings, # Передаем настройки mcpo
        }
    )

@router.get("/tools", response_class=HTMLResponse, name="ui_tools")
async def get_tools_page(request: Request, db: Session = Depends(get_session)):
    if not templates: raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info("UI Request: GET /tools (Страница доступных инструментов)")
    tools_data: Dict[str, Any] = {}; error_message: Optional[str] = None
    mcpo_settings = config_service.load_mcpo_settings()
    try:
        tools_data = await mcpo_service.get_aggregated_tools_from_mcpo(db)
    except Exception as e:
        logger.error(f"Ошибка получения агрегированных данных об инструментах: {e}", exc_info=True)
        error_message = "Произошла ошибка при получении информации об инструментах."
        tools_data = {"status": "ERROR", "servers": {}}
    return templates.TemplateResponse(
        "tools.html", {
            "request": request, "tools_data": tools_data,
            "error_message": error_message, "mcpo_port": mcpo_settings.port
        }
    )

# --- НОВЫЙ РОУТ: Страница логов MCPO ---
@router.get("/logs", response_class=HTMLResponse, name="ui_logs")
async def get_logs_page(
    request: Request,
    # Можно добавить параметры для количества строк и т.д. если нужно будет управлять с клиента
):
    """Отображает страницу с логами MCPO и настройками их автообновления."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info("UI Request: GET /logs (Страница логов MCPO)")

    mcpo_settings = config_service.load_mcpo_settings()
    # Логи будут загружаться через HTMX на самой странице logs.html,
    # здесь мы передаем только настройки для управления этим процессом
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "mcpo_settings": mcpo_settings, # Передаем все настройки, включая интервал
            "log_file_path_exists": os.path.exists(mcpo_settings.log_file_path) if mcpo_settings.log_file_path else False
        }
    )
# --- КОНЕЦ НОВОГО РОУТА ---

# --- Редактирование одиночного сервера (без изменений с прошлого шага) ---
@router.get("/servers/{server_id}/edit", response_class=HTMLResponse, name="ui_edit_server_form")
async def get_edit_server_form(request: Request, server_id: int, db: Session = Depends(get_session)):
    if not templates: raise HTTPException(status_code=500, detail="Templates not configured")
    definition_db = config_service.get_server_definition(db, server_id)
    if not definition_db: raise HTTPException(status_code=404, detail="Определение сервера не найдено")
    definition_data = ServerDefinitionRead.model_validate(definition_db).model_dump()
    return templates.TemplateResponse(
        "server_form.html",
        {"request": request, "server": definition_data, "error": None}
    )

@router.post("/servers/{server_id}/edit", name="ui_update_server")
async def handle_update_server_form(
    request: Request, server_id: int, db: Session = Depends(get_session),
    name: str = Form(...), server_type: str = Form(...), is_enabled: bool = Form(False),
    command: Optional[str] = Form(None), url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"),
    env_keys: List[str] = Form([], alias="env_key[]"),
    env_values: List[str] = Form([], alias="env_value[]")
):
    if not templates: raise HTTPException(status_code=500, detail="Templates not configured")
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip(): processed_env_vars[key_stripped] = value.strip()
    else: logger.warning(f"Расхождение в env_keys и env_values для ID сервера {server_id}")

    current_command = command; current_url = url
    final_args = processed_args; final_env_vars = processed_env_vars
    error_msg: Optional[str] = None

    if server_type == 'stdio': current_url = None
    elif server_type in ['sse', 'streamable_http']: current_command = None; final_args = []; final_env_vars = {}
    else: error_msg = "Неизвестный тип сервера."

    if not error_msg: # Продолжаем валидацию, если тип корректен
        if server_type == 'stdio' and not current_command: error_msg = "Поле 'Команда' обязательно для типа сервера 'stdio'."
        elif server_type in ['sse', 'streamable_http'] and not current_url: error_msg = f"Поле 'URL Сервера' обязательно для типа '{server_type}'."

    definition_in = ServerDefinitionUpdate(
        name=name, server_type=server_type, is_enabled=is_enabled,
        command=current_command, args=final_args, env_vars=final_env_vars, url=current_url
    )
    if error_msg:
        form_data_on_error = definition_in.model_dump(exclude_unset=True); form_data_on_error["id"] = server_id
        form_data_on_error["args"] = final_args # Передаем списком
        form_data_on_error["env_vars"] = final_env_vars # Передаем словарем
        return templates.TemplateResponse("server_form.html", {"request": request, "server": form_data_on_error, "error": error_msg}, status_code=400)
    try:
        updated = config_service.update_server_definition(db=db, server_id=server_id, definition_in=definition_in)
        if not updated: raise HTTPException(status_code=404, detail="Определение сервера не найдено для обновления")
        return RedirectResponse(url=router.url_path_for("ui_root"), status_code=303)
    except (ValueError, ValidationError) as e:
        form_data_on_error = definition_in.model_dump(exclude_unset=True); form_data_on_error["id"] = server_id
        form_data_on_error["args"] = final_args; form_data_on_error["env_vars"] = final_env_vars
        return templates.TemplateResponse("server_form.html", {"request": request, "server": form_data_on_error, "error": f"Не удалось обновить: {str(e)}"}, status_code=400)
    except Exception as e:
        form_data_on_error = definition_in.model_dump(exclude_unset=True); form_data_on_error["id"] = server_id
        form_data_on_error["args"] = final_args; form_data_on_error["env_vars"] = final_env_vars
        return templates.TemplateResponse("server_form.html", {"request": request, "server": form_data_on_error, "error": f"Непредвиденная ошибка: {str(e)}"}, status_code=500)

# --- Настройки MCPO ---
@router.get("/settings", response_class=HTMLResponse, name="ui_edit_mcpo_settings_form")
async def get_mcpo_settings_form(request: Request):
    if not templates: raise HTTPException(status_code=500, detail="Templates not configured")
    settings = config_service.load_mcpo_settings()
    return templates.TemplateResponse("mcpo_settings_form.html", {"request": request, "settings": settings.model_dump(), "error": None, "success": None})

@router.post("/settings", name="ui_update_mcpo_settings")
async def handle_update_mcpo_settings_form(
    request: Request,
    port: int = Form(...),
    api_key: Optional[str] = Form(None),
    use_api_key: bool = Form(False),
    config_file_path: str = Form(...),
    log_file_path: Optional[str] = Form(None),
    # Новые поля для настроек логов
    log_auto_refresh_enabled: bool = Form(False), # False если чекбокс не пришел
    log_auto_refresh_interval_seconds: int = Form(...)
):
    if not templates: raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info("UI Request: POST /settings (Обновление настроек MCPO)")
    error_msg: Optional[str] = None; success_msg: Optional[str] = None
    
    # Данные для отображения в форме в случае ошибки или успеха
    form_data_to_display = {
        "port": port, "api_key": api_key, "use_api_key": use_api_key,
        "config_file_path": config_file_path, "log_file_path": log_file_path,
        "log_auto_refresh_enabled": log_auto_refresh_enabled,
        "log_auto_refresh_interval_seconds": log_auto_refresh_interval_seconds
    }

    try:
        # Валидация (Pydantic модель McpoSettings сделает это при создании экземпляра)
        # Создаем экземпляр с новыми данными для валидации
        new_settings_data = {
            "port": port,
            "api_key": api_key if api_key and api_key.strip() else None,
            "use_api_key": use_api_key,
            "config_file_path": config_file_path,
            "log_file_path": log_file_path if log_file_path and log_file_path.strip() else None,
            "log_auto_refresh_enabled": log_auto_refresh_enabled,
            "log_auto_refresh_interval_seconds": log_auto_refresh_interval_seconds
        }
        updated_settings = McpoSettings(**new_settings_data) # Валидация здесь

        if config_service.save_mcpo_settings(updated_settings):
            success_msg = "Настройки MCPO успешно обновлены."
            logger.info(success_msg)
            form_data_to_display = updated_settings.model_dump() # Показываем сохраненные и валидированные
        else:
            error_msg = "Не удалось сохранить настройки MCPO."
            # form_data_to_display уже содержит введенные пользователем данные
            
    except ValidationError as ve: # Ошибки валидации Pydantic
        logger.warning(f"Ошибка валидации при обновлении настроек MCPO: {ve}")
        error_msg = "; ".join([f"{e['loc'][0] if e['loc'] else 'field'}: {e['msg']}" for e in ve.errors()])
        # form_data_to_display уже содержит введенные пользователем данные
    except ValueError as ve: # Другие ValueError (например, наш старый для порта)
        logger.warning(f"Ошибка значения при обновлении настроек MCPO: {ve}")
        error_msg = str(ve)
        # form_data_to_display уже содержит введенные пользователем данные

    return templates.TemplateResponse(
        "mcpo_settings_form.html",
        {"request": request, "settings": form_data_to_display, "error": error_msg, "success": success_msg}
    )


# --- Массовое добавление из JSON (без изменений) ---
@router.get("/servers/bulk_add", response_class=HTMLResponse, name="ui_bulk_add_form")
async def get_bulk_add_form(request: Request):
    if not templates: raise HTTPException(status_code=500, detail="Templates not configured")
    return templates.TemplateResponse("bulk_add_form.html", {"request": request, "error": None, "success_count": None, "config_json_str": ""})

@router.post("/servers/bulk_add", name="ui_process_bulk_add")
async def handle_bulk_add_form(
    request: Request, db: Session = Depends(get_session),
    config_json_str: str = Form(..., alias="configJsonStr"), default_enabled: bool = Form(False)
):
    # ... (код без изменений)
    if not templates: raise HTTPException(status_code=500, detail="Templates missing for bulk add form response")
    added_count = 0; errors: List[str] = []
    try: data = json.loads(config_json_str)
    except json.JSONDecodeError as e:
        return templates.TemplateResponse("bulk_add_form.html", {"request": request, "error": f"Некорректный JSON: {str(e)}", "success_count": 0, "config_json_str": config_json_str}, status_code=400)
    servers_to_process: Dict[str, Any] = {}
    if "mcpServers" in data and isinstance(data["mcpServers"], dict): servers_to_process = data["mcpServers"]
    elif isinstance(data, dict):
        all_values_are_dicts = all(isinstance(v, dict) for v in data.values())
        if all_values_are_dicts and data:
            first_val = next(iter(data.values()), None)
            if first_val and any(k in first_val for k in ["command", "args", "env", "type", "url"]): servers_to_process = data
            else: errors.append("Значения в словаре JSON не похожи на конфигурации серверов.")
        elif not data: errors.append("Предоставленный JSON пуст.")
        else: errors.append("Не все значения в словаре JSON являются объектами конфигурации.")
    else: errors.append("Корневой элемент JSON не является словарем.")
    if not servers_to_process and not errors: errors.append("Не найдено валидного блока 'mcpServers' или корневого объекта с конфигурациями.")

    for server_name, config_data_item in servers_to_process.items():
        if not isinstance(config_data_item, dict): errors.append(f"Конфигурация для '{server_name}' не является объектом."); continue
        initial_server_type = config_data_item.get("type"); initial_command = config_data_item.get("command"); initial_args = config_data_item.get("args", []); initial_env = config_data_item.get("env", {}); initial_url = config_data_item.get("url")
        final_server_type = initial_server_type
        if not final_server_type:
            if initial_command: final_server_type = "stdio"
            elif initial_url: final_server_type = "sse"
            else: errors.append(f"Не удалось определить тип для '{server_name}'."); continue
        final_command = initial_command; final_args = initial_args; final_env = initial_env; final_url = initial_url
        if final_server_type == 'stdio':
            final_url = None;
            if not final_command: errors.append(f"Команда обязательна для stdio сервера '{server_name}'."); continue
        elif final_server_type in ['sse', 'streamable_http']:
            final_command = None; final_args = []; final_env = {};
            if not final_url: errors.append(f"URL обязателен для {final_server_type} сервера '{server_name}'."); continue
        else: errors.append(f"Неизвестный тип сервера '{final_server_type}' для '{server_name}'."); continue
        try:
            definition_in = ServerDefinitionCreate(name=server_name, is_enabled=default_enabled, server_type=final_server_type, command=final_command, args=final_args, env_vars=final_env, url=final_url)
            config_service.create_server_definition(db=db, definition_in=definition_in); added_count += 1
        except (ValueError, ValidationError) as e: msg = f"Ошибка добавления '{server_name}': {str(e)}"; errors.append(msg); logger.error(msg)
        except Exception as e: msg = f"Непредвиденная ошибка при добавлении '{server_name}': {str(e)}"; errors.append(msg); logger.error(msg, exc_info=True)
    final_error_message = "; ".join(errors) if errors else None
    if added_count > 0 and not final_error_message :
        return RedirectResponse(url=router.url_path_for("ui_root"), status_code=303)
    else:
        if not final_error_message and added_count == 0 and not servers_to_process and not data: final_error_message = "Предоставленный JSON пуст или не содержит конфигураций серверов."
        elif not final_error_message and added_count == 0: final_error_message = "Серверы не были добавлены. Проверьте формат или убедитесь, что имена уникальны."
        return templates.TemplateResponse("bulk_add_form.html", {"request": request, "error": final_error_message, "success_count": added_count, "config_json_str": config_json_str}, status_code=400 if final_error_message else 200)