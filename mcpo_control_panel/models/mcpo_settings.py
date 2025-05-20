# ================================================
# FILE: mcpo_control_panel/models/mcpo_settings.py
# ================================================
from pydantic import BaseModel, Field, field_validator, PositiveInt, HttpUrl
from typing import Optional, List, Dict
import os
from urllib.parse import urlparse
from pathlib import Path

def _get_default_data_dir_for_settings() -> Path:
    return Path(os.getenv("MCPO_MANAGER_DATA_DIR_EFFECTIVE", Path.home() / ".mcpo_manager_data"))

class McpoSettings(BaseModel):
    """Model for storing mcpo and UI manager settings."""
    port: int = Field(default=8000, description="Port on which mcpo will run")
    api_key: Optional[str] = Field(default=None, description="API key for protecting mcpo endpoints")
    use_api_key: bool = Field(default=False, description="Whether to use API key when starting mcpo")
    config_file_path: str = Field(
        default_factory=lambda: str(_get_default_data_dir_for_settings() / "mcp_generated_config.json"),
        description="Path for saving the generated mcpo config file. Defaults to a path within the MCPO_MANAGER_DATA_DIR_EFFECTIVE."
    )
    log_file_path: Optional[str] = Field(
        default_factory=lambda: str(_get_default_data_dir_for_settings() / "mcpo_manager.log"),
        description="Path to the log file for mcpo. If empty, logs may not be saved. Defaults to a path within the MCPO_MANAGER_DATA_DIR_EFFECTIVE."
    )

    public_base_url: Optional[str] = Field(
        default=None,
        description="Public base URL where MCPO is accessible (e.g., http://example.com:8000). Used for generating tool links. If not set, http://127.0.0.1:PORT is used."
    )

    log_auto_refresh_enabled: bool = Field(
        default=True,
        description="Enable automatic refresh of the logs block on the logs page"
    )
    log_auto_refresh_interval_seconds: PositiveInt = Field(
        default=5,
        description="Log auto-refresh interval in seconds (min: 5, max: 3600)"
    )

    health_check_enabled: bool = Field(
        default=True,
        description="Enable periodic health checks for mcpo"
    )
    health_check_interval_seconds: PositiveInt = Field(
        default=10,
        description="Interval between successful health checks (in seconds, min: 5)"
    )
    health_check_failure_attempts: PositiveInt = Field(
        default=3,
        description="Number of consecutive failed checks before attempting restart (min: 1)"
    )
    health_check_failure_retry_delay_seconds: PositiveInt = Field(
        default=5,
        description="Delay between failed check attempts (in seconds, min: 1)"
    )
    auto_restart_on_failure: bool = Field(
        default=True,
        description="Automatically restart mcpo after specified number of failed checks"
    )

    # New field for manual configuration mode
    manual_config_mode_enabled: bool = Field(
        default=False,
        description="Enable manual editing of the MCPO config file. Disables UI server management and automated config generation from UI definitions."
    )
    root_path: str = Field(
        default="", 
        description="The root path for the application if running behind a reverse proxy under a subpath (e.g., /mcpmanager)."
    )

    @field_validator('port')
    @classmethod
    def check_port_range(cls, value: int) -> int:
        if not (1024 <= value <= 65535):
            raise ValueError('Port must be in the range from 1024 to 65535.')
        return value

    @field_validator('log_auto_refresh_interval_seconds')
    @classmethod
    def check_log_interval(cls, value: PositiveInt) -> PositiveInt:
        if not (5 <= value <= 3600):
            raise ValueError('Log auto-refresh interval must be between 5 and 3600 seconds.')
        return value

    @field_validator('health_check_interval_seconds')
    @classmethod
    def check_health_interval(cls, value: PositiveInt) -> PositiveInt:
        if value < 5:
            raise ValueError('Health check interval must be at least 5 seconds.')
        return value

    @field_validator('health_check_failure_attempts')
    @classmethod
    def check_health_failure_attempts(cls, value: PositiveInt) -> PositiveInt:
        if value < 1:
            raise ValueError('Number of check attempts before restart must be at least 1.')
        return value

    @field_validator('health_check_failure_retry_delay_seconds')
    @classmethod
    def check_health_retry_delay(cls, value: PositiveInt) -> PositiveInt:
        if value < 1:
            raise ValueError('Delay between failed checks must be at least 1 second.')
        return value

    @field_validator('public_base_url')
    @classmethod
    def check_public_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        
        cleaned_value = value.strip().rstrip('/')

        if not cleaned_value:
            return None

        parsed = urlparse(cleaned_value)
        if not parsed.scheme or not parsed.netloc:
             raise ValueError('Public base URL must be a valid URL (e.g., http://example.com:8000).')
        if parsed.scheme not in ('http', 'https'):
            raise ValueError('Public base URL must use http or https scheme.')
        
        return cleaned_value

    INTERNAL_ECHO_SERVER_NAME: str = "echo-mcp-server-for-testing"
    INTERNAL_ECHO_SERVER_COMMAND: str = "uvx"
    INTERNAL_ECHO_SERVER_ARGS: List[str] = ["echo-mcp-server-for-testing"]
    INTERNAL_ECHO_SERVER_ENV: Dict[str, str] = {"MCP_MANAGER_HEALTH_CHECK": "true"}
    INTERNAL_ECHO_TOOL_PATH: str = "/echo_tool"
    INTERNAL_ECHO_PAYLOAD: Dict[str, str] = {"message": "mcp_manager_health_check_ping"}