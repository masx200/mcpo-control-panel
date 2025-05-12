# ================================================
# FILE: src/mcp_manager_ui/models/mcpo_settings.py
# (Добавляем поля для настроек автообновления логов)
# ================================================
from pydantic import BaseModel, Field, field_validator
from typing import Optional
import os

class McpoSettings(BaseModel):
    """Модель для хранения настроек запуска mcpo и UI."""
    port: int = Field(default=8000, description="Порт, на котором будет запущен mcpo")
    api_key: Optional[str] = Field(default=None, description="API ключ для защиты mcpo эндпоинтов")
    use_api_key: bool = Field(default=False, description="Использовать ли API ключ при запуске mcpo")
    config_file_path: str = Field(default="mcp_generated_config.json", description="Путь для сохранения генерируемого mcpo config файла")
    log_file_path: Optional[str] = Field(default="mcpo_manager.log", description="Путь к файлу логов для mcpo")

    # Новые поля для настроек автообновления логов
    log_auto_refresh_enabled: bool = Field(
        default=True,
        description="Включить автоматическое обновление блока логов на странице логов"
    )
    log_auto_refresh_interval_seconds: int = Field(
        default=180, # 3 минуты
        description="Интервал автообновления логов в секундах (мин: 5, макс: 3600)"
    )

    @field_validator('log_auto_refresh_interval_seconds')
    @classmethod
    def check_log_interval(cls, value: int) -> int:
        if not (5 <= value <= 3600):
            raise ValueError('Интервал автообновления логов должен быть между 5 и 3600 секундами.')
        return value