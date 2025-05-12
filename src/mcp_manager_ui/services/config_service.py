# ================================================
# FILE: src/mcp_manager_ui/services/config_service.py
# (Без существенных изменений, Pydantic обработает новые поля)
# ================================================
import json
import logging
import os
import sys
from typing import List, Optional, Dict, Any

from sqlmodel import Session, select, SQLModel

from ..models.server_definition import (
    ServerDefinition, ServerDefinitionCreate, ServerDefinitionUpdate
)
from ..models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
SETTINGS_FILE = "mcpo_manager_settings.json" # Файл для настроек McpoSettings

# --- Функции для работы с McpoSettings ---
def load_mcpo_settings() -> McpoSettings:
    """Загружает настройки McpoSettings из JSON файла. Если файл не найден или ошибка, возвращает дефолтные."""
    if not os.path.exists(SETTINGS_FILE):
        logger.warning(f"Файл настроек {SETTINGS_FILE} не найден. Используются настройки по умолчанию.")
        default_settings = McpoSettings()
        # Сохраняем дефолтные настройки при первом запуске, чтобы файл был создан
        save_mcpo_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings_data = json.load(f)
            # Pydantic попытается заполнить новые поля значениями по умолчанию, если их нет в файле
            settings = McpoSettings(**settings_data)
            logger.info(f"Настройки mcpo загружены из {SETTINGS_FILE}")
            # Если после загрузки какие-то поля не установились (например, старый файл не содержал их),
            # и мы хотим принудительно сохранить файл с полным набором полей:
            # save_mcpo_settings(settings) # Раскомментировать, если нужно обновить файл при загрузке
            return settings
    except (IOError, json.JSONDecodeError, TypeError, ValidationError) as e: # Добавил ValidationError
        logger.error(f"Ошибка загрузки или парсинга файла настроек {SETTINGS_FILE}: {e}. Используются настройки по умолчанию.", exc_info=True)
        # В случае ошибки также возвращаем и сохраняем дефолтные
        default_settings = McpoSettings()
        save_mcpo_settings(default_settings) # Перезаписываем некорректный файл дефолтными
        return default_settings

def save_mcpo_settings(settings: McpoSettings) -> bool:
    """Сохраняет настройки McpoSettings в JSON файл."""
    logger.info(f"Сохранение настроек mcpo в {SETTINGS_FILE}")
    try:
        with open(SETTINGS_FILE, 'w') as f:
            # mode='json' для правильной сериализации, exclude_none=True чтобы не писать null поля, если они Optional
            json.dump(settings.model_dump(mode='json', exclude_none=True), f, indent=2)
        logger.info(f"Настройки mcpo успешно сохранены.")
        return True
    except IOError as e:
        logger.error(f"Ошибка записи файла настроек mcpo в {SETTINGS_FILE}: {e}")
        return False
    except Exception as e: # Общий обработчик на всякий случай
        logger.error(f"Непредвиденная ошибка при сохранении настроек mcpo: {e}", exc_info=True)
        return False

# --- CRUD операции для ServerDefinition (без изменений) ---
def create_server_definition(db: Session, *, definition_in: ServerDefinitionCreate) -> ServerDefinition:
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
    logger.info(f"Удаление определения сервера с ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return False
    db.delete(db_definition)
    db.commit()
    logger.info(f"Определение сервера с ID {server_id} удалено.")
    return True

def toggle_server_enabled(db: Session, server_id: int) -> Optional[ServerDefinition]:
    logger.info(f"Переключение 'is_enabled' для определения сервера ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return None
    db_definition.is_enabled = not db_definition.is_enabled
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Определение сервера '{db_definition.name}' is_enabled установлено в: {db_definition.is_enabled}")
    return db_definition

def generate_mcpo_config_file(db: Session, output_path: str) -> bool:
    logger.info(f"Генерация файла конфигурации mcpo: {output_path}")
    enabled_definitions = get_server_definitions(db, only_enabled=True, limit=10000) # Увеличен лимит на всякий случай
    mcp_servers_config = {}
    current_platform = sys.platform
    for definition in enabled_definitions:
        config_entry: Dict[str, Any] = {}
        if definition.server_type == "stdio":
            original_command = definition.command
            original_args = definition.args if definition.args is not None else []
            original_env = definition.env_vars if definition.env_vars is not None else {}
            if not original_command:
                logger.warning(f"Пропуск stdio определения '{definition.name}': команда отсутствует."); continue
            # Windows adaptation logic (можно вынести в отдельную функцию)
            if current_platform == "win32" and os.path.basename(original_command).lower() == "npx":
                adapted_command = "npx.cmd"
                config_entry["command"] = adapted_command
                if original_args: config_entry["args"] = original_args
            elif current_platform == "win32" and original_command.lower() == "npx.cmd":
                 config_entry["command"] = original_command
                 if original_args: config_entry["args"] = original_args
            # Check for existing "cmd /c npx" to avoid double adaptation
            elif current_platform == "win32" and original_command.lower() == "cmd" and original_args and original_args[0].lower() == "/c" and "npx" in original_args[1].lower():
                config_entry["command"] = original_command
                config_entry["args"] = original_args
            else:
                config_entry["command"] = original_command
                if original_args: config_entry["args"] = original_args
            if original_env: config_entry["env"] = original_env
        elif definition.server_type in ["sse", "streamable_http"]:
            if not definition.url:
                logger.warning(f"Пропуск {definition.server_type} определения '{definition.name}': URL отсутствует."); continue
            config_entry["type"] = definition.server_type
            config_entry["url"] = definition.url
        else:
            logger.warning(f"Пропуск определения '{definition.name}': Неизвестный тип сервера '{definition.server_type}'"); continue
        mcp_servers_config[definition.name] = config_entry
    final_config = {"mcpServers": mcp_servers_config}
    try:
        with open(output_path, 'w', encoding='utf-8') as f: # Добавляем encoding
            json.dump(final_config, f, indent=2, ensure_ascii=False) # ensure_ascii для русских имен
        logger.info(f"Файл конфигурации mcpo успешно сгенерирован с {len(mcp_servers_config)} серверами.")
        return True
    except IOError as e:
        logger.error(f"Ошибка записи файла конфигурации mcpo в {output_path}: {e}")
        return False