# ================================================
# FILE: src/mcp_manager_ui/services/config_service.py
# (Обновляем _build_mcp_servers_config_dict для расширенной адаптации Windows)
# ================================================
import json
import logging
import os
# import sys # Больше не нужен
from typing import List, Optional, Dict, Any

from sqlmodel import Session, select, SQLModel
from pydantic import ValidationError

from ..models.server_definition import (
    ServerDefinition, ServerDefinitionCreate, ServerDefinitionUpdate
)
from ..models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
SETTINGS_FILE = "mcpo_manager_settings.json"

# --- Функции для работы с McpoSettings (без изменений) ---
def load_mcpo_settings() -> McpoSettings:
    # ... (код без изменений) ...
    if not os.path.exists(SETTINGS_FILE):
        logger.warning(f"Файл настроек {SETTINGS_FILE} не найден. Используются настройки по умолчанию.")
        default_settings = McpoSettings()
        save_mcpo_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings_data = json.load(f)
            settings = McpoSettings(**settings_data)
            logger.info(f"Настройки mcpo загружены из {SETTINGS_FILE}")
            return settings
    except (IOError, json.JSONDecodeError, TypeError, ValidationError) as e:
        logger.error(f"Ошибка загрузки или парсинга файла настроек {SETTINGS_FILE}: {e}. Используются настройки по умолчанию.", exc_info=True)
        default_settings = McpoSettings()
        save_mcpo_settings(default_settings)
        return default_settings


def save_mcpo_settings(settings: McpoSettings) -> bool:
    # ... (код без изменений) ...
    logger.info(f"Сохранение настроек mcpo в {SETTINGS_FILE}")
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings.model_dump(mode='json', exclude_none=True), f, indent=2)
        logger.info(f"Настройки mcpo успешно сохранены.")
        return True
    except IOError as e:
        logger.error(f"Ошибка записи файла настроек mcpo в {SETTINGS_FILE}: {e}")
        return False
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при сохранении настроек mcpo: {e}", exc_info=True)
        return False

# --- CRUD операции для ServerDefinition (без изменений) ---
def create_server_definition(db: Session, *, definition_in: ServerDefinitionCreate) -> ServerDefinition:
    # ... (код без изменений) ...
    logger.info(f"Создание определения сервера: {definition_in.name}")
    existing = db.exec(select(ServerDefinition).where(ServerDefinition.name == definition_in.name)).first()
    if existing:
        raise ValueError(f"Определение сервера с именем '{definition_in.name}' уже существует.")
    db_definition = ServerDefinition.model_validate(definition_in)
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Определение сервера '{db_definition.name}' создано с ID: {db_definition.id}")
    return db_definition

def get_server_definition(db: Session, server_id: int) -> Optional[ServerDefinition]:
    # ... (код без изменений) ...
    logger.debug(f"Получение определения сервера с ID: {server_id}")
    statement = select(ServerDefinition).where(ServerDefinition.id == server_id)
    definition = db.exec(statement).first()
    if not definition: logger.warning(f"Определение сервера с ID {server_id} не найдено.")
    return definition

def get_server_definitions(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    only_enabled: bool = False
) -> List[ServerDefinition]:
    # ... (код без изменений) ...
    log_msg = f"Получение определений серверов (skip={skip}, limit={limit}"
    statement = select(ServerDefinition)
    if only_enabled:
        statement = statement.where(ServerDefinition.is_enabled == True)
        log_msg += ", только включенные=True"
    statement = statement.order_by(ServerDefinition.name).offset(skip).limit(limit)
    log_msg += ")"
    logger.debug(log_msg)
    definitions = db.exec(statement).all()
    return definitions

def update_server_definition(db: Session, *, server_id: int, definition_in: ServerDefinitionUpdate) -> Optional[ServerDefinition]:
    # ... (код без изменений) ...
    logger.info(f"Обновление определения сервера с ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return None
    update_data = definition_in.model_dump(exclude_unset=True)
    logger.debug(f"Данные для обновления сервера ID {server_id}: {update_data}")
    if "name" in update_data and update_data["name"] != db_definition.name:
        existing = db.exec(select(ServerDefinition).where(ServerDefinition.name == update_data["name"])).first()
        if existing:
            raise ValueError(f"Определение сервера с именем '{update_data['name']}' уже существует.")
    for key, value in update_data.items():
         setattr(db_definition, key, value)
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Определение сервера '{db_definition.name}' обновлено.")
    return db_definition

def delete_server_definition(db: Session, server_id: int) -> bool:
    # ... (код без изменений) ...
    logger.info(f"Удаление определения сервера с ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return False
    db.delete(db_definition)
    db.commit()
    logger.info(f"Определение сервера с ID {server_id} удалено.")
    return True

def toggle_server_enabled(db: Session, server_id: int) -> Optional[ServerDefinition]:
    # ... (код без изменений) ...
    logger.info(f"Переключение 'is_enabled' для определения сервера ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return None
    db_definition.is_enabled = not db_definition.is_enabled
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Определение сервера '{db_definition.name}' is_enabled установлено в: {db_definition.is_enabled}")
    return db_definition

# --- Генерация конфигурационного файла mcpo ---
def _build_mcp_servers_config_dict(db: Session, settings: McpoSettings, adapt_for_windows: bool = False) -> Dict[str, Any]:
    """Вспомогательная функция для построения словаря mcpServers."""
    enabled_definitions = get_server_definitions(db, only_enabled=True, limit=10000)
    mcp_servers_config: Dict[str, Any] = {}

    for definition in enabled_definitions:
        config_entry: Dict[str, Any] = {}
        if definition.name == settings.INTERNAL_ECHO_SERVER_NAME and settings.health_check_enabled:
            logger.warning(f"[Config Builder] Определение сервера '{definition.name}' конфликтует с именем внутреннего эхо-сервера и будет проигнорировано в пользу эхо-сервера.")
            continue

        if definition.server_type == "stdio":
            original_command = definition.command
            original_args = definition.args if definition.args is not None else []
            original_env = definition.env_vars if definition.env_vars is not None else {}
            if not original_command:
                logger.warning(f"[Config Builder] Пропуск stdio определения '{definition.name}': команда отсутствует."); continue

            # Инициализируем значениями по умолчанию
            command_to_use = original_command
            args_to_use = original_args

            # --- ИЗМЕНЕНИЕ: Расширенная логика адаптации для Windows ---
            if adapt_for_windows:
                command_basename_lower = os.path.basename(original_command).lower()

                if command_basename_lower == "npx":
                    command_to_use = "cmd"
                    # Формируем новые аргументы: /c npx -y [original_args]
                    args_to_use = ["/c", "npx", "-y"] + original_args
                    logger.debug(f"[Config Builder] Адаптация '{original_command}' для Windows: 'cmd /c npx -y {' '.join(original_args)}' для сервера '{definition.name}'")

                elif command_basename_lower == "uvx":
                    command_to_use = "cmd"
                    # Формируем новые аргументы: /c uvx [original_args]
                    args_to_use = ["/c", "uvx"] + original_args
                    logger.debug(f"[Config Builder] Адаптация '{original_command}' для Windows: 'cmd /c uvx {' '.join(original_args)}' для сервера '{definition.name}'")
                # Можно добавить elif для других команд при необходимости
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            config_entry["command"] = command_to_use
            if args_to_use: config_entry["args"] = args_to_use # Используем адаптированные или оригинальные args
            if original_env: config_entry["env"] = original_env

        elif definition.server_type in ["sse", "streamable_http"]:
            if not definition.url:
                logger.warning(f"[Config Builder] Пропуск {definition.server_type} определения '{definition.name}': URL отсутствует."); continue
            config_entry["type"] = definition.server_type
            config_entry["url"] = definition.url
        else:
            logger.warning(f"[Config Builder] Пропуск определения '{definition.name}': Неизвестный тип сервера '{definition.server_type}'"); continue

        mcp_servers_config[definition.name] = config_entry

    # Добавляем внутренний эхо-сервер для health check, если включено
    if settings.health_check_enabled:
        if settings.INTERNAL_ECHO_SERVER_NAME in mcp_servers_config:
            logger.warning(
                f"[Config Builder] Имя внутреннего эхо-сервера '{settings.INTERNAL_ECHO_SERVER_NAME}' уже используется "
                f"пользовательским определением. Health check может работать некорректно или использовать "
                f"пользовательский сервер вместо встроенного тестового."
            )

        # Инициализируем конфиг эхо-сервера
        echo_server_command = settings.INTERNAL_ECHO_SERVER_COMMAND
        echo_server_args = settings.INTERNAL_ECHO_SERVER_ARGS

        # --- ИЗМЕНЕНИЕ: Адаптация эхо-сервера для Windows ---
        if adapt_for_windows:
            echo_command_basename_lower = os.path.basename(echo_server_command).lower()
            if echo_command_basename_lower == "npx":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "npx", "-y"] + settings.INTERNAL_ECHO_SERVER_ARGS
                logger.debug(f"[Config Builder] Адаптация команды эхо-сервера '{settings.INTERNAL_ECHO_SERVER_COMMAND}' для Windows")
            elif echo_command_basename_lower == "uvx":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "uvx"] + settings.INTERNAL_ECHO_SERVER_ARGS
                logger.debug(f"[Config Builder] Адаптация команды эхо-сервера '{settings.INTERNAL_ECHO_SERVER_COMMAND}' для Windows")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        echo_server_config = {
            "command": echo_server_command,
            "args": echo_server_args,
        }
        if settings.INTERNAL_ECHO_SERVER_ENV:
             echo_server_config["env"] = settings.INTERNAL_ECHO_SERVER_ENV

        mcp_servers_config[settings.INTERNAL_ECHO_SERVER_NAME] = echo_server_config
        logger.info(f"[Config Builder] Внутренний эхо-сервер '{settings.INTERNAL_ECHO_SERVER_NAME}' добавлен в конфигурацию (адаптация для Windows: {'Да' if adapt_for_windows else 'Нет'}).")

    return mcp_servers_config


def generate_mcpo_config_file(db: Session, settings: McpoSettings) -> bool:
    """
    Генерирует **стандартный** конфигурационный файл для mcpo и записывает его на диск.
    Не применяет адаптации для Windows.
    """
    output_path = settings.config_file_path
    logger.info(f"Генерация стандартного файла конфигурации mcpo: {output_path}")

    try:
        mcp_servers_config = _build_mcp_servers_config_dict(db, settings, adapt_for_windows=False)
        final_config = {"mcpServers": mcp_servers_config}

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_config, f, indent=2, ensure_ascii=False)
        logger.info(f"Стандартный файл конфигурации mcpo успешно сгенерирован с {len(mcp_servers_config)} серверами.")
        return True
    except Exception as e:
        logger.error(f"Ошибка при генерации или записи стандартного файла конфигурации mcpo в {output_path}: {e}", exc_info=True)
        return False

def generate_mcpo_config_content_for_windows(db: Session, settings: McpoSettings) -> str:
    """
    Генерирует содержимое конфигурационного файла mcpo с адаптациями для Windows.
    Возвращает JSON строку.
    """
    logger.info(f"Генерация содержимого конфигурации mcpo для Windows (без записи файла)...")
    try:
        mcp_servers_config = _build_mcp_servers_config_dict(db, settings, adapt_for_windows=True)
        final_config = {"mcpServers": mcp_servers_config}
        config_json_string = json.dumps(final_config, indent=2, ensure_ascii=False)
        logger.info(f"Содержимое конфигурации для Windows успешно сгенерировано с {len(mcp_servers_config)} серверами.")
        return config_json_string
    except Exception as e:
        logger.error(f"Ошибка при генерации содержимого конфигурации mcpo для Windows: {e}", exc_info=True)
        error_message = f"Ошибка генерации конфига для Windows: {e}"
        # Возвращаем JSON-комментарий с ошибкой
        return f"// {error_message}"