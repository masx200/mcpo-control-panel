# mcpo_control_panel/services/config_managers/settings_manager.py
import json
import logging
import os
from pathlib import Path
from pydantic import ValidationError

from ...models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
SETTINGS_FILE_NAME = "mcpo_manager_settings.json"

def _get_data_dir() -> Path:
    return Path(os.getenv("MCPO_MANAGER_DATA_DIR_EFFECTIVE", Path.home() / ".mcpo_manager_data"))

def _get_settings_file_path() -> Path:
    return _get_data_dir() / SETTINGS_FILE_NAME

def load_mcpo_settings() -> McpoSettings:
    settings_file_path = _get_settings_file_path()
    if not settings_file_path.exists():
        logger.warning(f"Settings file {settings_file_path} not found. Using default settings.")
        default_settings = McpoSettings(config_file_path=str(_get_data_dir() / "mcp_generated_config.json"))
        save_mcpo_settings(default_settings) # Save defaults if file not found
        return default_settings
    try:
        with open(settings_file_path, 'r') as f:
            settings_data = json.load(f)
            # Ensure config_file_path is correctly initialized relative to data_dir logic
            if "config_file_path" not in settings_data or not settings_data.get("config_file_path"):
                logger.info(f"Missing 'config_file_path' in settings, re-initializing to default name within data_dir: {_get_data_dir()}")
                settings_data["config_file_path"] = "mcp_generated_config.json"
            elif not Path(settings_data["config_file_path"]).is_absolute():
                original_path = settings_data["config_file_path"]
                filename_only = Path(original_path).name
                if original_path != filename_only:
                    logger.info(f"Relative 'config_file_path' ('{original_path}') found in settings, storing only filename: '{filename_only}' for consistency.")
                settings_data["config_file_path"] = filename_only
            
            settings = McpoSettings(**settings_data)
            logger.info(f"MCPO settings loaded from {settings_file_path}")
            return settings
    except (IOError, json.JSONDecodeError, TypeError, ValidationError) as e:
        logger.error(f"Error loading or parsing settings file {settings_file_path}: {e}. Using default settings.", exc_info=True)
        default_settings = McpoSettings(config_file_path="mcp_generated_config.json") # Default filename
        save_mcpo_settings(default_settings)
        return default_settings

def save_mcpo_settings(settings: McpoSettings) -> bool:
    settings_file_path = _get_settings_file_path()
    logger.info(f"Saving MCPO settings to {settings_file_path}")
    try:
        settings_file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_file_path, 'w') as f:
            json.dump(settings.model_dump(mode='json', exclude_none=True), f, indent=2)
        logger.info(f"MCPO settings successfully saved to {settings_file_path}")
        return True
    except IOError as e:
        logger.error(f"Error writing MCPO settings file to {settings_file_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error when saving MCPO settings to {settings_file_path}: {e}", exc_info=True)
        return False