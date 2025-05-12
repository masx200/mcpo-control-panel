# src/mcp_manager_ui/models/mcpo_settings.py
# ================================================
# FILE: src/mcp_manager_ui/models/mcpo_settings.py
# (Добавляем public_base_url)
# ================================================
from pydantic import BaseModel, Field, field_validator, PositiveInt, HttpUrl
from typing import Optional, List, Dict
import os
from urllib.parse import urlparse # Для базовой валидации URL

class McpoSettings(BaseModel):
    """Модель для хранения настроек запуска mcpo и UI менеджера."""
    port: int = Field(default=8000, description="Порт, на котором будет запущен mcpo")
    api_key: Optional[str] = Field(default=None, description="API ключ для защиты mcpo эндпоинтов")
    use_api_key: bool = Field(default=False, description="Использовать ли API ключ при запуске mcpo")
    config_file_path: str = Field(
        default="mcpo_manager_data/mcp_generated_config.json",
        description="Путь для сохранения генерируемого mcpo config файла"
    )
    log_file_path: Optional[str] = Field(
        default="mcpo_manager.log",
        description="Путь к файлу логов для mcpo. Если пустой, логи могут не сохраняться."
    )

    # --- НОВОЕ ПОЛЕ ---
    public_base_url: Optional[str] = Field(
        default=None,
        description="Публичный базовый URL, по которому доступен MCPO (напр., http://example.com:8000). Используется для генерации ссылок на инструменты. Если не задан, используется http://127.0.0.1:PORT."
    )

    # Настройки отображения логов
    log_auto_refresh_enabled: bool = Field(
        default=True,
        description="Включить автоматическое обновление блока логов на странице логов"
    )
    log_auto_refresh_interval_seconds: PositiveInt = Field(
        default=5, # 5 секунд
        description="Интервал автообновления логов в секундах (мин: 5, макс: 3600)"
    )

    # Поля для Health Check (без изменений)
    health_check_enabled: bool = Field(
        default=False,
        description="Включить периодическую проверку работоспособности mcpo"
    )
    health_check_interval_seconds: PositiveInt = Field(
        default=10,
        description="Интервал между успешными проверками работоспособности (в секундах, мин: 5)"
    )
    health_check_failure_attempts: PositiveInt = Field(
        default=3,
        description="Количество последовательных неудачных проверок перед попыткой перезапуска (мин: 1)"
    )
    health_check_failure_retry_delay_seconds: PositiveInt = Field(
        default=5,
        description="Задержка между неудачными попытками проверки (в секундах, мин: 1)"
    )
    auto_restart_on_failure: bool = Field(
        default=False,
        description="Автоматически перезапускать mcpo после заданного числа неудачных проверок"
    )

    # --- ВАЛИДАТОРЫ (добавляем для public_base_url) ---
    @field_validator('port')
    @classmethod
    def check_port_range(cls, value: int) -> int:
        if not (1024 <= value <= 65535):
            raise ValueError('Порт должен быть в диапазоне от 1024 до 65535.')
        return value

    @field_validator('log_auto_refresh_interval_seconds')
    @classmethod
    def check_log_interval(cls, value: PositiveInt) -> PositiveInt:
        if not (5 <= value <= 3600): # 1 час
            raise ValueError('Интервал автообновления логов должен быть между 5 и 3600 секундами.')
        return value

    # --- Валидаторы Health Check (без изменений) ---
    @field_validator('health_check_interval_seconds')
    @classmethod
    def check_health_interval(cls, value: PositiveInt) -> PositiveInt:
        if value < 5:
            raise ValueError('Интервал проверки работоспособности должен быть не менее 5 секунд.')
        return value

    @field_validator('health_check_failure_attempts')
    @classmethod
    def check_health_failure_attempts(cls, value: PositiveInt) -> PositiveInt:
        if value < 1:
            raise ValueError('Количество попыток проверки перед перезапуском должно быть не менее 1.')
        return value

    @field_validator('health_check_failure_retry_delay_seconds')
    @classmethod
    def check_health_retry_delay(cls, value: PositiveInt) -> PositiveInt:
        if value < 1:
            raise ValueError('Задержка между неудачными проверками должна быть не менее 1 секунды.')
        return value

    # --- НОВЫЙ ВАЛИДАТОР для public_base_url ---
    @field_validator('public_base_url')
    @classmethod
    def check_public_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None # Пустое значение разрешено
        
        # Убираем лишние пробелы и слеш в конце
        cleaned_value = value.strip().rstrip('/')

        if not cleaned_value: # Если после очистки осталась пустая строка
            return None

        parsed = urlparse(cleaned_value)
        if not parsed.scheme or not parsed.netloc:
             raise ValueError('Публичный базовый URL должен быть валидным URL (напр., http://example.com:8000).')
        if parsed.scheme not in ('http', 'https'):
            raise ValueError('Публичный базовый URL должен использовать схему http или https.')
        
        # Возвращаем очищенное значение без слеша в конце
        return cleaned_value


    # Константы для захардкоженных параметров health check (без изменений)
    INTERNAL_ECHO_SERVER_NAME: str = "echo-mcp-server-for-testing"
    INTERNAL_ECHO_SERVER_COMMAND: str = "uvx"
    INTERNAL_ECHO_SERVER_ARGS: List[str] = ["echo-mcp-server-for-testing"]
    INTERNAL_ECHO_SERVER_ENV: Dict[str, str] = {"MCP_MANAGER_HEALTH_CHECK": "true"}
    INTERNAL_ECHO_TOOL_PATH: str = "/echo_tool"
    INTERNAL_ECHO_PAYLOAD: Dict[str, str] = {"message": "mcp_manager_health_check_ping"}