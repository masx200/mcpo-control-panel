# ================================================
# FILE: src/mcp_manager_ui/ui/routes.py
# (Redirect after successful add, pass success info via query params)
# ================================================
import html
import logging
import json
import os
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote # For URL encoding server names

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session

from ..api.mcpo_control import get_mcpo_settings_dependency

from ..db.database import get_session
from ..services import config_service, mcpo_service
from ..models.server_definition import (
    ServerDefinitionCreate, ServerDefinitionUpdate, ServerDefinitionRead
)
from ..models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None # Устанавливается из main.py

# --- Вспомогательная функция для де-адаптации Windows команд ---
# ... (функция _deadapt_windows_command без изменений) ...
def _deadapt_windows_command(command: Optional[str], args: List[str]) -> Tuple[Optional[str], List[str]]:
    """Преобразует 'cmd /c npx/uvx ...' обратно в 'npx/uvx ...'."""
    if command == "cmd" and args and args[0].lower() == "/c" and len(args) > 1:
        executable = args[1].lower()
        if executable == "npx":
            args_start_index = 2
            if len(args) > 2 and args[2] == "-y":
                args_start_index = 3
            new_command = "npx"
            new_args = args[args_start_index:]
            logger.debug(f"Де-адаптация Windows: 'cmd /c npx...' -> '{new_command} {' '.join(new_args)}'")
            return new_command, new_args
        elif executable == "uvx":
            new_command = "uvx"
            new_args = args[2:]
            logger.debug(f"Де-адаптация Windows: 'cmd /c uvx...' -> '{new_command} {' '.join(new_args)}'")
            return new_command, new_args
    return command, args


# --- Основные страницы UI ---
# ... (get_index_page, get_tools_page, show_logs_page без изменений) ...
@router.get("/", response_class=HTMLResponse, name="ui_root")
async def get_index_page(request: Request, db: Session = Depends(get_session)):
    """Отображает главную страницу со списком серверов и управлением MCPO."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    server_definitions = config_service.get_server_definitions(db)
    definitions_read = [ServerDefinitionRead.model_validate(d) for d in server_definitions]
    current_mcpo_status = mcpo_service.get_mcpo_status()
    mcpo_settings = config_service.load_mcpo_settings()

    # Получаем сообщения из query params (для тостов после редиректа)
    single_add_success = request.query_params.get("single_add_success")
    bulk_success_count = request.query_params.get("bulk_success")


    return templates.TemplateResponse(
        "index.html", {
            "request": request,
            "server_definitions": definitions_read,
            "mcpo_status": current_mcpo_status,
            "mcpo_settings": mcpo_settings, # Передаем актуальные настройки
            # Передаем данные для тостов в шаблон (если они есть)
            "single_add_success_msg": single_add_success,
            "bulk_success_msg": bulk_success_count,
        })

@router.get("/tools", response_class=HTMLResponse, name="ui_tools")
async def get_tools_page(request: Request, db: Session = Depends(get_session)):
    """Отображает страницу с доступными инструментами из запущенного MCPO."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    tools_data: Dict[str, Any] = {}
    error_message: Optional[str] = None

    try:
        # Эта функция теперь возвращает словарь, включающий 'base_url_for_links'
        tools_data = await mcpo_service.get_aggregated_tools_from_mcpo(db)
    except Exception as e:
        logger.error(f"Ошибка получения агрегированных данных об инструментах: {e}", exc_info=True)
        error_message = "Произошла ошибка при получении информации об инструментах."
        # Устанавливаем дефолтное значение, если произошла ошибка
        tools_data = {"status": "ERROR", "servers": {}, "base_url_for_links": f"http://127.0.0.1:{config_service.load_mcpo_settings().port}"}

    return templates.TemplateResponse(
        "tools.html", {
            "request": request,
            "tools_data": tools_data, # Содержит статус, серверы и base_url_for_links
            "error_message": error_message
        })



@router.get("/logs", response_class=HTMLResponse, name="ui_logs")
async def show_logs_page(
    request: Request,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """
    Отображает страницу логов. Сами логи будут загружены через HTMX.
    """
    logger.debug("UI Request: GET /logs page")
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    # Проверяем, существует ли файл логов, чтобы показать статус в шаблоне
    log_file_path_exists = False
    if settings.log_file_path and os.path.exists(settings.log_file_path):
        log_file_path_exists = True
        logger.debug(f"Log file '{settings.log_file_path}' exists.")
    elif settings.log_file_path:
        logger.warning(f"Log file path configured ('{settings.log_file_path}') but file does not exist.")
    else:
        logger.info("Log file path is not configured.")

    # Передаем настройки и статус существования файла в шаблон
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "mcpo_settings": settings, # Настройки нужны для пути и интервала обновления
        "log_file_path_exists": log_file_path_exists # Флаг существования файла
    })


# --- Редактирование одиночного сервера ---
# ... (get_edit_server_form, handle_update_server_form без изменений) ...
@router.get("/servers/{server_id}/edit", response_class=HTMLResponse, name="ui_edit_server_form")
async def get_edit_server_form(request: Request, server_id: int, db: Session = Depends(get_session)):
    """Отображает страницу редактирования определения сервера."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    definition_db = config_service.get_server_definition(db, server_id)
    if not definition_db:
        raise HTTPException(status_code=404, detail="Определение сервера не найдено")

    definition_data = ServerDefinitionRead.model_validate(definition_db).model_dump()
    action_url = request.url_for("ui_update_server", server_id=server_id)
    form_title = f"Редактирование '{definition_data.get('name', '')}'"
    submit_button_text = "Обновить определение"

    return templates.TemplateResponse("edit_server_page.html", {
        "request": request,
        "action_url": action_url,
        "submit_button_text": submit_button_text,
        "server_data": definition_data,
        "form_title": form_title,
        "is_add_form": False,
        "cancel_url": request.url_for("ui_root") # Добавляем URL для кнопки "Отмена"
    })

@router.post("/servers/{server_id}/edit", name="ui_update_server")
async def handle_update_server_form(
    request: Request, server_id: int, db: Session = Depends(get_session),
    # Параметры из формы
    name: str = Form(...),
    server_type: str = Form(...),
    is_enabled: bool = Form(False),
    command: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"), # Динамические поля аргументов
    env_keys: List[str] = Form([], alias="env_key[]"),   # Динамические поля переменных окружения (ключи)
    env_values: List[str] = Form([], alias="env_value[]") # Динамические поля переменных окружения (значения)
):
    """Обрабатывает данные из формы редактирования определения сервера."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info(f"UI Request: POST /servers/{server_id}/edit (Обновление сервера)")

    # Обработка и валидация данных формы
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip():
                processed_env_vars[key_stripped] = value.strip()
    else:
        logger.warning(f"Расхождение в количестве env_keys и env_values при обновлении сервера ID {server_id}")

    current_command = command if command and command.strip() else None
    current_url = url if url and url.strip() else None
    final_args = processed_args
    final_env_vars = processed_env_vars
    error_msg: Optional[str] = None

    # Очистка полей в зависимости от типа сервера
    if server_type == 'stdio':
        current_url = None
    elif server_type in ['sse', 'streamable_http']:
        current_command = None
        final_args = []
        final_env_vars = {}
    else:
        error_msg = "Неизвестный тип сервера."

    # Проверка обязательных полей для типа
    if not error_msg:
        if server_type == 'stdio' and not current_command:
            error_msg = "Поле 'Команда' обязательно для типа 'stdio'."
        elif server_type in ['sse', 'streamable_http'] and not current_url:
            error_msg = f"Поле 'URL' обязательно для типа '{server_type}'."

    # Данные для ререндера формы в случае ошибки
    form_data_on_error = {
        "id": server_id,
        "name": name,
        "server_type": server_type,
        "is_enabled": is_enabled,
        "command": current_command,
        "args": final_args,
        "env_vars": final_env_vars,
        "url": current_url
    }
    action_url = request.url_for("ui_update_server", server_id=server_id)
    form_title = f"Редактирование '{name}' (Ошибка)"
    submit_button_text = "Обновить определение"

    # Если есть ошибка валидации, рендерим форму снова с ошибкой
    if error_msg:
        return templates.TemplateResponse("edit_server_page.html", {
            "request": request,
            "action_url": action_url,
            "submit_button_text": submit_button_text,
            "server_data": form_data_on_error,
            "form_title": form_title,
            "is_add_form": False,
            "error": error_msg,
            "cancel_url": request.url_for("ui_root")
            }, status_code=400)

    # Попытка обновить данные в БД
    try:
        definition_in = ServerDefinitionUpdate(
            name=name,
            server_type=server_type,
            is_enabled=is_enabled,
            command=current_command,
            args=final_args,
            env_vars=final_env_vars,
            url=current_url
        )
        updated = config_service.update_server_definition(db=db, server_id=server_id, definition_in=definition_in)

        if not updated:
            # Сервер не найден во время обновления
            return templates.TemplateResponse("edit_server_page.html", {
                "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
                "server_data": form_data_on_error, "form_title": f"Редактирование '{name}' (Не найден)",
                "is_add_form": False, "error": "Определение сервера не найдено для обновления.",
                "cancel_url": request.url_for("ui_root")
                }, status_code=404)

        # Успешное обновление -> Редирект на главную
        logger.info(f"Определение сервера ID {server_id} успешно обновлено.")
        # --- ИЗМЕНЕНИЕ: Добавляем query param при редиректе ---
        redirect_url = str(request.url_for("ui_root")) + f"?update_success={quote(updated.name)}"
        return RedirectResponse(url=redirect_url, status_code=303)

    except (ValueError, ValidationError) as e:
        # Ошибка бизнес-логики (например, дубликат имени) или валидации Pydantic
        error_text = f"Не удалось обновить: {str(e)}"
        logger.warning(f"Ошибка при обновлении сервера ID {server_id}: {error_text}")
        return templates.TemplateResponse("edit_server_page.html", {
            "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
            "server_data": form_data_on_error, "form_title": form_title,
            "is_add_form": False, "error": error_text,
            "cancel_url": request.url_for("ui_root")
            }, status_code=400)
    except Exception as e:
        # Непредвиденная ошибка сервера
        logger.error(f"Непредвиденная ошибка при обновлении сервера ID {server_id}: {e}", exc_info=True)
        return templates.TemplateResponse("edit_server_page.html", {
             "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
             "server_data": form_data_on_error, "form_title": f"Редактирование '{name}' (Серверная ошибка)",
             "is_add_form": False, "error": "Непредвиденная ошибка сервера.",
             "cancel_url": request.url_for("ui_root")
             }, status_code=500)

# --- Настройки MCPO ---
# ... (get_mcpo_settings_form, handle_update_mcpo_settings_form без изменений) ...
@router.get("/settings", response_class=HTMLResponse, name="ui_edit_mcpo_settings_form")
async def get_mcpo_settings_form(request: Request):
    """Отображает страницу с формой настроек MCPO."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    settings = config_service.load_mcpo_settings()
    return templates.TemplateResponse("mcpo_settings_form.html", {
        "request": request,
        "settings": settings.model_dump(), # Передаем словарь для формы
        "error": None,
        "success": None
    })

@router.post("/settings", name="ui_update_mcpo_settings")
async def handle_update_mcpo_settings_form(
    request: Request,
    # Параметры из формы настроек MCPO
    port: int = Form(...),
    public_base_url: Optional[str] = Form(None), # Новое поле
    api_key: Optional[str] = Form(None),
    use_api_key: bool = Form(False),
    config_file_path: str = Form(...),
    log_file_path: Optional[str] = Form(None),
    log_auto_refresh_enabled: bool = Form(False),
    log_auto_refresh_interval_seconds: int = Form(...),
    health_check_enabled: bool = Form(False),
    health_check_interval_seconds: int = Form(...),
    health_check_failure_attempts: int = Form(...),
    health_check_failure_retry_delay_seconds: int = Form(...),
    auto_restart_on_failure: bool = Form(False)
):
    """Обрабатывает данные из формы настроек MCPO."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info("UI Request: POST /settings (Обновление всех настроек MCPO)")

    error_msg: Optional[str] = None
    success_msg: Optional[str] = None

    # Собираем данные из формы для отображения (даже если будет ошибка валидации)
    form_data_to_display = {
        "port": port,
        "public_base_url": public_base_url,
        "api_key": api_key,
        "use_api_key": use_api_key,
        "config_file_path": config_file_path,
        "log_file_path": log_file_path,
        "log_auto_refresh_enabled": log_auto_refresh_enabled,
        "log_auto_refresh_interval_seconds": log_auto_refresh_interval_seconds,
        "health_check_enabled": health_check_enabled,
        "health_check_interval_seconds": health_check_interval_seconds,
        "health_check_failure_attempts": health_check_failure_attempts,
        "health_check_failure_retry_delay_seconds": health_check_failure_retry_delay_seconds,
        "auto_restart_on_failure": auto_restart_on_failure
    }

    try:
        # Очистка опциональных строковых полей
        clean_api_key = api_key if api_key and api_key.strip() else None
        clean_log_file_path = log_file_path if log_file_path and log_file_path.strip() else None
        clean_public_base_url = public_base_url if public_base_url and public_base_url.strip() else None

        # Создаем экземпляр модели для валидации
        settings_for_validation = McpoSettings(
            port=port,
            public_base_url=clean_public_base_url, # Используем очищенное значение
            api_key=clean_api_key,
            use_api_key=use_api_key,
            config_file_path=config_file_path,
            log_file_path=clean_log_file_path,
            log_auto_refresh_enabled=log_auto_refresh_enabled,
            log_auto_refresh_interval_seconds=log_auto_refresh_interval_seconds,
            health_check_enabled=health_check_enabled,
            health_check_interval_seconds=health_check_interval_seconds,
            health_check_failure_attempts=health_check_failure_attempts,
            health_check_failure_retry_delay_seconds=health_check_failure_retry_delay_seconds,
            auto_restart_on_failure=auto_restart_on_failure
        )

        # Сохраняем валидные настройки через сервис
        if config_service.save_mcpo_settings(settings_for_validation):
            success_msg = "Настройки MCPO успешно обновлены."
            logger.info(success_msg)
            # Обновляем данные для отображения уже валидированными значениями
            form_data_to_display = settings_for_validation.model_dump()
        else:
            error_msg = "Не удалось сохранить настройки MCPO."

    except ValidationError as ve:
        # Ошибка валидации Pydantic
        logger.warning(f"Ошибка валидации при обновлении настроек MCPO: {ve.errors(include_url=False)}")
        error_details = [
            f"Поле '{'.'.join(map(str, e['loc'])) if e['loc'] else 'field'}': {e['msg']}"
            for e in ve.errors(include_url=False)
        ]
        error_msg = "Ошибки валидации: " + "; ".join(error_details)
    except Exception as e:
        # Непредвиденная ошибка
        logger.error(f"Непредвиденная ошибка при обновлении настроек: {e}", exc_info=True)
        error_msg = f"Произошла непредвиденная ошибка: {str(e)}"

    # Рендерим ту же страницу с результатом
    return templates.TemplateResponse(
        "mcpo_settings_form.html",
        {"request": request, "settings": form_data_to_display, "error": error_msg, "success": success_msg}
    )


# --- Массовое и одиночное добавление серверов ---

@router.get("/servers/bulk_add", response_class=HTMLResponse, name="ui_bulk_add_form")
async def get_bulk_add_form(
    request: Request,
    # Параметры для возможного ререндера после POST запроса
    bulk_config_json_str: Optional[str] = None,
    bulk_error: Optional[str] = None,
    bulk_success_count: Optional[int] = None,
    single_server_error: Optional[str] = None,
    single_server_form_data: Optional[dict] = None
    ):
    """Отображает страницу с формами массового и одиночного добавления."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    # URL для формы добавления одного сервера (используется в шаблоне)
    single_add_action_url = request.url_for("ui_add_single_server")

    return templates.TemplateResponse("bulk_add_form.html", {
        "request": request,
        # Для массовой формы
        "config_json_str": bulk_config_json_str or "",
        "bulk_error": bulk_error,
        "bulk_success_count": bulk_success_count,
        # Для одиночной формы
        "single_server_form_data": single_server_form_data or None,
        "single_server_error": single_server_error,
        "single_add_action_url": single_add_action_url
        })

@router.post("/servers/bulk_add", name="ui_process_bulk_add")
async def handle_bulk_add_form(
    request: Request, db: Session = Depends(get_session),
    config_json_str: str = Form(..., alias="configJsonStr"), # JSON из textarea
    default_enabled: bool = Form(False) # Состояние чекбокса "включить"
):
    """Обрабатывает данные из формы массового добавления JSON."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates missing for bulk add form response")
    logger.info("UI Request: POST /servers/bulk_add (Обработка JSON)")

    added_count = 0
    errors: List[str] = []
    added_names: List[str] = [] # Список имен добавленных серверов

    try:
        data = json.loads(config_json_str)
    except json.JSONDecodeError as e:
        errors.append(f"Некорректный JSON: {str(e)}")
        # Рендерим ту же страницу с ошибкой JSON
        single_add_action_url = request.url_for("ui_add_single_server")
        return templates.TemplateResponse("bulk_add_form.html", {
             "request": request, "config_json_str": config_json_str,
             "bulk_error": "; ".join(errors), "bulk_success_count": 0,
             "single_server_form_data": None, "single_server_error": None,
             "single_add_action_url": single_add_action_url
             }, status_code=400)

    # --- (Определение формата JSON и извлечение серверов без изменений) ---
    servers_to_process: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(data, dict):
        target_dict = data.get("mcpServers", data)
        if isinstance(target_dict, dict):
            servers_to_process = list(target_dict.items())
        else:
            errors.append("Ожидался словарь в ключе 'mcpServers' или корневой словарь.")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "name" in item:
                server_name = str(item["name"])
                servers_to_process.append((server_name, item))
            else:
                errors.append("Элемент в списке JSON не является объектом с полем 'name'.")
    else:
        errors.append("Неподдерживаемый формат JSON. Ожидается объект или список объектов.")

    if not servers_to_process and not errors:
        errors.append("Не найдено серверов для обработки в предоставленном JSON.")

    # --- (Обработка каждого найденного сервера без изменений, кроме добавления в added_names) ---
    for server_name, config_data_item in servers_to_process:
        if not isinstance(config_data_item, dict):
            errors.append(f"Конфигурация для сервера '{server_name}' не является объектом."); continue

        original_command = config_data_item.get("command")
        original_args = config_data_item.get("args", [])
        original_env = config_data_item.get("env", {})
        original_url = config_data_item.get("url")

        final_command, final_args = _deadapt_windows_command(original_command, original_args)
        final_env = original_env
        final_url = original_url

        final_server_type = None
        if final_command:
            final_server_type = "stdio"
            final_url = None
        elif final_url:
            final_server_type = config_data_item.get("type", "sse")
            if final_server_type not in ["sse", "streamable_http"]:
                logger.warning(f"Неизвестный тип '{final_server_type}' для URL-сервера '{server_name}', используется 'sse'.")
                final_server_type = "sse"
            final_command = None; final_args = []; final_env = {}
        else:
            errors.append(f"Не удалось определить тип (отсутствует 'command' или 'url') для сервера '{server_name}'."); continue

        try:
            definition_in = ServerDefinitionCreate(
                name=server_name,
                is_enabled=default_enabled,
                server_type=final_server_type,
                command=final_command,
                args=final_args,
                env_vars=final_env,
                url=final_url
            )
            created_server = config_service.create_server_definition(db=db, definition_in=definition_in)
            added_count += 1
            added_names.append(created_server.name) # Сохраняем имя
        except (ValueError, ValidationError) as e:
            msg = f"Ошибка добавления сервера '{server_name}': {str(e)}"
            errors.append(msg); logger.warning(msg)
        except Exception as e:
            msg = f"Непредвиденная ошибка при добавлении сервера '{server_name}': {str(e)}"
            errors.append(msg); logger.error(msg, exc_info=True)

    # --- (Формирование результата и редирект/рендер формы без изменений, кроме передачи count в редиректе) ---
    final_error_message = "; ".join(errors) if errors else None

    if added_count > 0 and not final_error_message:
        logger.info(f"Успешно добавлено {added_count} серверов из JSON: {', '.join(added_names)}")
        # --- ИЗМЕНЕНИЕ: Редирект с количеством ---
        redirect_url = str(request.url_for("ui_root")) + f"?bulk_success={added_count}"
        return RedirectResponse(url=redirect_url, status_code=303)
    else:
        if not final_error_message and added_count == 0 and servers_to_process:
             final_error_message = "Серверы не были добавлены. Возможные причины: уже существуют, ошибки валидации (см. выше)."
        elif not final_error_message and added_count == 0 and not servers_to_process and not errors:
            final_error_message = "В предоставленном JSON не найдено серверов для обработки."

        logger.warning(f"Завершено массовое добавление с {added_count} успехами и ошибками: {final_error_message}")
        single_add_action_url = request.url_for("ui_add_single_server")
        return templates.TemplateResponse("bulk_add_form.html", {
             "request": request, "config_json_str": config_json_str,
             "bulk_error": final_error_message, "bulk_success_count": added_count,
             "single_server_form_data": None, "single_server_error": None,
             "single_add_action_url": single_add_action_url
             }, status_code=400 if final_error_message else 200)


@router.post("/servers/add_single", name="ui_add_single_server")
async def handle_add_single_server_form(
    request: Request, db: Session = Depends(get_session),
    # Параметры из формы одиночного добавления (аналогично редактированию)
    name: str = Form(...),
    server_type: str = Form(...),
    is_enabled: bool = Form(False),
    command: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"),
    env_keys: List[str] = Form([], alias="env_key[]"),
    env_values: List[str] = Form([], alias="env_value[]")
):
    """Обрабатывает данные из формы добавления одного сервера."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info(f"UI Request: POST /servers/add_single (Добавление сервера '{name}')")

    # --- (Обработка и валидация данных без изменений) ---
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip():
                processed_env_vars[key_stripped] = value.strip()
    else:
        logger.warning("Расхождение в количестве env_keys и env_values при добавлении сервера")

    final_command = command if command and command.strip() else None
    final_args = processed_args
    final_env_vars = processed_env_vars
    final_url = url if url and url.strip() else None
    error_msg: Optional[str] = None

    final_command, final_args = _deadapt_windows_command(final_command, final_args)

    if server_type == 'stdio':
        final_url = None
    elif server_type in ['sse', 'streamable_http']:
        final_command = None; final_args = []; final_env_vars = {}
    else:
        error_msg = "Неизвестный тип сервера."

    if not error_msg:
        if server_type == 'stdio' and not final_command:
            error_msg = "Поле 'Команда' обязательно для типа 'stdio'."
        elif server_type in ['sse', 'streamable_http'] and not final_url:
            error_msg = f"Поле 'URL' обязательно для типа '{server_type}'."

    # --- (Данные для ререндера формы без изменений) ---
    form_data_on_error = {
        "name": name, "server_type": server_type, "is_enabled": is_enabled,
        "command": final_command, "args": final_args,
        "env_vars": final_env_vars, "url": final_url
    }
    single_add_action_url = request.url_for("ui_add_single_server") # URL для ререндера

    # --- (Если ошибка валидации, рендерим bulk_add_form без изменений) ---
    if error_msg:
        return templates.TemplateResponse("bulk_add_form.html", {
            "request": request,
            "config_json_str": "", "bulk_error": None, "bulk_success_count": None, # Очищаем массовую форму
            "single_server_form_data": form_data_on_error,
            "single_server_error": error_msg,
            "single_add_action_url": single_add_action_url
            }, status_code=400)

    # --- (Пытаемся создать определение сервера в БД без изменений, кроме редиректа) ---
    try:
        definition_in = ServerDefinitionCreate(
            name=name,
            server_type=server_type,
            is_enabled=is_enabled,
            command=final_command,
            args=final_args,
            env_vars=final_env_vars,
            url=final_url
        )
        created = config_service.create_server_definition(db=db, definition_in=definition_in)

        # --- ИЗМЕНЕНИЕ: Успешное добавление -> Редирект на главную с именем сервера ---
        logger.info(f"Успешно добавлено определение сервера '{created.name}' (ID: {created.id}).")
        # Кодируем имя сервера для безопасной передачи в URL
        redirect_url = str(request.url_for("ui_root")) + f"?single_add_success={quote(created.name)}"
        return RedirectResponse(url=redirect_url, status_code=303)

    except (ValueError, ValidationError) as e:
        # --- (Обработка ошибок без изменений) ---
        error_text = f"Не удалось добавить сервер: {str(e)}"
        logger.warning(f"Ошибка при добавлении сервера '{name}': {error_text}")
        return templates.TemplateResponse("bulk_add_form.html", {
            "request": request,
            "config_json_str": "", "bulk_error": None, "bulk_success_count": None,
            "single_server_form_data": form_data_on_error,
            "single_server_error": error_text,
            "single_add_action_url": single_add_action_url
            }, status_code=400)
    except Exception as e:
        # --- (Обработка ошибок без изменений) ---
        logger.error(f"Непредвиденная ошибка при добавлении сервера '{name}': {e}", exc_info=True)
        return templates.TemplateResponse("bulk_add_form.html", {
             "request": request,
             "config_json_str": "", "bulk_error": None, "bulk_success_count": None,
             "single_server_form_data": form_data_on_error,
             "single_server_error": "Непредвиденная ошибка сервера при добавлении.",
             "single_add_action_url": single_add_action_url
             }, status_code=500)