# ================================================
# FILE: src/mcp_manager_ui/models/mcpo_settings.py
# ================================================
from pydantic import BaseModel, Field, field_validator, PositiveInt, HttpUrl
from typing import Optional, List, Dict
import os
from urllib.parse import urlparse  # For basic URL validation

class McpoSettings(BaseModel):
    """Model for storing mcpo and UI manager settings."""
    port: int = Field(default=8000, description="Port on which mcpo will run")
    api_key: Optional[str] = Field(default=None, description="API key for protecting mcpo endpoints")
    use_api_key: bool = Field(default=False, description="Whether to use API key when starting mcpo")
    config_file_path: str = Field(
        default="mcpo_manager_data/mcp_generated_config.json",
        description="Path for saving the generated mcpo config file"
    )
    log_file_path: Optional[str] = Field(
        default="mcpo_manager.log",
        description="Path to the log file for mcpo. If empty, logs may not be saved."
    )

    public_base_url: Optional[str] = Field(
        default=None,
        description="Public base URL where MCPO is accessible (e.g., http://example.com:8000). Used for generating tool links. If not set, http://127.0.0.1:PORT is used."
    )

    # Log display settings
    log_auto_refresh_enabled: bool = Field(
        default=True,
        description="Enable automatic refresh of the logs block on the logs page"
    )
    log_auto_refresh_interval_seconds: PositiveInt = Field(
        default=5,  # 5 seconds
        description="Log auto-refresh interval in seconds (min: 5, max: 3600)"
    )

    # Health Check fields
    health_check_enabled: bool = Field(
        default=False,
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
        default=False,
        description="Automatically restart mcpo after specified number of failed checks"
    )

    # Validators
    @field_validator('port')
    @classmethod
    def check_port_range(cls, value: int) -> int:
        if not (1024 <= value <= 65535):
            raise ValueError('Port must be in the range from 1024 to 65535.')
        return value

    @field_validator('log_auto_refresh_interval_seconds')
    @classmethod
    def check_log_interval(cls, value: PositiveInt) -> PositiveInt:
        if not (5 <= value <= 3600):  # 1 hour
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
            return None  # Empty value is allowed
        
        # Remove extra spaces and trailing slash
        cleaned_value = value.strip().rstrip('/')

        if not cleaned_value:  # If empty string after cleaning
            return None

        parsed = urlparse(cleaned_value)
        if not parsed.scheme or not parsed.netloc:
             raise ValueError('Public base URL must be a valid URL (e.g., http://example.com:8000).')
        if parsed.scheme not in ('http', 'https'):
            raise ValueError('Public base URL must use http or https scheme.')
        
        # Return cleaned value without trailing slash
        return cleaned_value

    # Constants for hardcoded health check parameters
    INTERNAL_ECHO_SERVER_NAME: str = "echo-mcp-server-for-testing"
    INTERNAL_ECHO_SERVER_COMMAND: str = "uvx"
    INTERNAL_ECHO_SERVER_ARGS: List[str] = ["echo-mcp-server-for-testing"]
    INTERNAL_ECHO_SERVER_ENV: Dict[str, str] = {"MCP_MANAGER_HEALTH_CHECK": "true"}
    INTERNAL_ECHO_TOOL_PATH: str = "/echo_tool"
    INTERNAL_ECHO_PAYLOAD: Dict[str, str] = {"message": "mcp_manager_health_check_ping"}