# ================================================
# FILE: src/mcp_manager_ui/models/server_definition.py
# (Проверяем и уточняем Pydantic модели для args и env_vars)
# ================================================
import json
from typing import Optional, List, Dict, Any
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON # Используем JSON тип SQLAlchemy

# Модель для таблицы БД (без изменений, уже должна быть корректной)
class ServerDefinition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, description="Уникальное имя для идентификации в UI и как ключ в mcpServers конфиге")
    is_enabled: bool = Field(default=False, index=True, description="Включить это определение в генерируемый mcpo_config.json?")
    server_type: str = Field(description="Тип MCP сервера ('stdio', 'sse', 'streamable_http')")

    # Поля для stdio
    command: Optional[str] = Field(default=None, description="Команда для запуска")
    # args и env_vars будут храниться как JSON в БД
    args: Optional[List[str]] = Field(default_factory=list, sa_column=Column(JSON), description="Аргументы команды")
    env_vars: Optional[Dict[str, str]] = Field(default_factory=dict, sa_column=Column(JSON), description="Переменные окружения")

    # Поля для sse / streamable_http
    url: Optional[str] = Field(default=None, description="URL эндпоинта MCP сервера")


# --- Pydantic модели для API и валидации форм ---

class ServerDefinitionBase(SQLModel):
    """Базовая модель с полями, общими для создания и чтения."""
    name: str
    is_enabled: bool = False
    server_type: str # 'stdio', 'sse', 'streamable_http'
    command: Optional[str] = None
    # Используем List[str] и Dict[str, str] напрямую для Pydantic моделей
    args: List[str] = Field(default_factory=list)
    env_vars: Dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None

    # Можно добавить model_validator для проверки полей в зависимости от server_type,
    # если это не делается в обработчике роута.
    # Пример:
    # from pydantic import model_validator
    # @model_validator(mode='after')
    # def check_type_specific_fields(self) -> 'ServerDefinitionBase':
    #     if self.server_type == 'stdio':
    #         if not self.command:
    #             raise ValueError("Поле 'command' обязательно для типа 'stdio'")
    #         self.url = None # Очищаем ненужное поле
    #     elif self.server_type in ['sse', 'streamable_http']:
    #         if not self.url:
    #             raise ValueError(f"Поле 'url' обязательно для типа '{self.server_type}'")
    #         self.command = None # Очищаем ненужные поля
    #         self.args = []
    #         self.env_vars = {}
    #     else:
    #         raise ValueError(f"Неизвестный тип сервера: {self.server_type}")
    #     return self

class ServerDefinitionCreate(ServerDefinitionBase):
    """Модель для данных при создании нового определения (API/форма)."""
    pass

class ServerDefinitionRead(ServerDefinitionBase):
    """Модель для возврата данных определения из API (включает ID)."""
    id: int

class ServerDefinitionUpdate(SQLModel):
    """
    Модель для частичного обновления определения (API/форма).
    Все поля опциональны.
    """
    name: Optional[str] = None
    is_enabled: Optional[bool] = None
    server_type: Optional[str] = None
    command: Optional[str] = None
    # При обновлении мы также ожидаем полные списки/словари,
    # а не частичные изменения внутри них.
    args: Optional[List[str]] = None
    env_vars: Optional[Dict[str, str]] = None
    url: Optional[str] = None