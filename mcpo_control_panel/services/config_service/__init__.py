# mcpo_control_panel/services/config_managers/__init__.py
from .settings_manager import load_mcpo_settings, save_mcpo_settings
from .definition_manager import (
    create_server_definition,
    get_server_definition,
    get_server_definitions,
    update_server_definition,
    delete_server_definition,
    toggle_server_enabled,
)
from .file_generator import (
    generate_mcpo_config_file,
    generate_mcpo_config_content_for_windows,
    analyze_bulk_server_definitions,
    # If _deadapt_windows_command or _extract_servers_from_json are needed externally:
    # _deadapt_windows_command,
    # _extract_servers_from_json,
)

__all__ = [
    "load_mcpo_settings",
    "save_mcpo_settings",
    "create_server_definition",
    "get_server_definition",
    "get_server_definitions",
    "update_server_definition",
    "delete_server_definition",
    "toggle_server_enabled",
    "generate_mcpo_config_file",
    "generate_mcpo_config_content_for_windows",
    "analyze_bulk_server_definitions",
]