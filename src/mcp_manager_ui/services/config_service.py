# ================================================
# FILE: src/mcp_manager_ui/services/config_service.py
# ================================================
import json
import logging
import os
from typing import List, Optional, Dict, Any, TypedDict, Tuple

from sqlmodel import Session, select, SQLModel
from pydantic import ValidationError

from ..models.server_definition import (
    ServerDefinition, ServerDefinitionCreate, ServerDefinitionUpdate
)
from ..models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
SETTINGS_FILE = "mcpo_manager_settings.json"

class InvalidServerInfo(TypedDict): # Unchanged
    name: Optional[str]
    data: Dict[str, Any]
    error: str

class AnalysisResult(TypedDict): # Unchanged
    valid_new: List[ServerDefinitionCreate]
    existing: List[str]
    invalid: List[InvalidServerInfo]


# --- McpoSettings functions ---
def load_mcpo_settings() -> McpoSettings:
    if not os.path.exists(SETTINGS_FILE):
        logger.warning(f"Settings file {SETTINGS_FILE} not found. Using default settings.")
        default_settings = McpoSettings()
        save_mcpo_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings_data = json.load(f)
            settings = McpoSettings(**settings_data)
            logger.info(f"MCPO settings loaded from {SETTINGS_FILE}")
            return settings
    except (IOError, json.JSONDecodeError, TypeError, ValidationError) as e:
        logger.error(f"Error loading or parsing settings file {SETTINGS_FILE}: {e}. Using default settings.", exc_info=True)
        default_settings = McpoSettings()
        save_mcpo_settings(default_settings)
        return default_settings


def save_mcpo_settings(settings: McpoSettings) -> bool:
    logger.info(f"Saving MCPO settings to {SETTINGS_FILE}")
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings.model_dump(mode='json', exclude_none=True), f, indent=2)
        logger.info(f"MCPO settings successfully saved.")
        return True
    except IOError as e:
        logger.error(f"Error writing MCPO settings file to {SETTINGS_FILE}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error when saving MCPO settings: {e}", exc_info=True)
        return False

# --- ServerDefinition CRUD operations ---
def create_server_definition(db: Session, *, definition_in: ServerDefinitionCreate) -> ServerDefinition:
    logger.info(f"Creating server definition: {definition_in.name}")
    existing = db.exec(select(ServerDefinition).where(ServerDefinition.name == definition_in.name)).first()
    if existing:
        raise ValueError(f"Server definition with name '{definition_in.name}' already exists.")
    db_definition = ServerDefinition.model_validate(definition_in)
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Server definition '{db_definition.name}' created with ID: {db_definition.id}")
    return db_definition

def get_server_definition(db: Session, server_id: int) -> Optional[ServerDefinition]:
    logger.debug(f"Getting server definition with ID: {server_id}")
    statement = select(ServerDefinition).where(ServerDefinition.id == server_id)
    definition = db.exec(statement).first()
    if not definition: logger.warning(f"Server definition with ID {server_id} not found.")
    return definition

def get_server_definitions(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    only_enabled: bool = False
) -> List[ServerDefinition]:
    log_msg = f"Getting server definitions (skip={skip}, limit={limit}"
    statement = select(ServerDefinition)
    if only_enabled:
        statement = statement.where(ServerDefinition.is_enabled == True)
        log_msg += ", only_enabled=True"
    statement = statement.order_by(ServerDefinition.name).offset(skip).limit(limit)
    log_msg += ")"
    logger.debug(log_msg)
    definitions = db.exec(statement).all()
    return definitions

def _deadapt_windows_command(command: Optional[str], args: List[str]) -> Tuple[Optional[str], List[str]]:
    """Converts 'cmd /c npx/uvx/docker ...' back to 'npx/uvx/docker ...'."""
    if command == "cmd" and args and args[0].lower() == "/c" and len(args) > 1:
        executable = args[1].lower()
        if executable == "npx":
            args_start_index = 2
            if len(args) > 2 and args[2] == "-y":
                args_start_index = 3
            new_command = "npx"
            new_args = args[args_start_index:]
            logger.debug(f"De-adapting Windows: 'cmd /c npx...' -> '{new_command} {' '.join(new_args)}'")
            return new_command, new_args
        elif executable == "uvx":
            new_command = "uvx"
            new_args = args[2:]
            logger.debug(f"De-adapting Windows: 'cmd /c uvx...' -> '{new_command} {' '.join(new_args)}'")
            return new_command, new_args
        elif executable == "docker":
            new_command = "docker"
            new_args = args[2:]
            logger.debug(f"De-adapting Windows: 'cmd /c docker...' -> '{new_command} {' '.join(new_args)}'")
            return new_command, new_args
    return command, args

def update_server_definition(db: Session, *, server_id: int, definition_in: ServerDefinitionUpdate) -> Optional[ServerDefinition]:
    logger.info(f"Updating server definition with ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return None
    update_data = definition_in.model_dump(exclude_unset=True)
    logger.debug(f"Update data for server ID {server_id}: {update_data}")
    if "name" in update_data and update_data["name"] != db_definition.name:
        existing = db.exec(select(ServerDefinition).where(ServerDefinition.name == update_data["name"])).first()
        if existing:
            raise ValueError(f"Server definition with name '{update_data['name']}' already exists.")
    for key, value in update_data.items():
         setattr(db_definition, key, value)
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Server definition '{db_definition.name}' updated.")
    return db_definition

def delete_server_definition(db: Session, server_id: int) -> bool:
    logger.info(f"Deleting server definition with ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return False
    db.delete(db_definition)
    db.commit()
    logger.info(f"Server definition with ID {server_id} deleted.")
    return True

def toggle_server_enabled(db: Session, server_id: int) -> Optional[ServerDefinition]:
    logger.info(f"Toggling 'is_enabled' for server definition ID: {server_id}")
    db_definition = get_server_definition(db, server_id)
    if not db_definition: return None
    db_definition.is_enabled = not db_definition.is_enabled
    db.add(db_definition)
    db.commit()
    db.refresh(db_definition)
    logger.info(f"Server definition '{db_definition.name}' is_enabled set to: {db_definition.is_enabled}")
    return db_definition

# --- MCPO configuration file generation ---
def _build_mcp_servers_config_dict(db: Session, settings: McpoSettings, adapt_for_windows: bool = False) -> Dict[str, Any]:
    """Helper function to build the mcpServers dictionary."""
    enabled_definitions = get_server_definitions(db, only_enabled=True, limit=10000)
    mcp_servers_config: Dict[str, Any] = {}

    for definition in enabled_definitions:
        config_entry: Dict[str, Any] = {}
        if definition.name == settings.INTERNAL_ECHO_SERVER_NAME and settings.health_check_enabled:
            logger.warning(f"[Config Builder] Server definition '{definition.name}' conflicts with internal echo server name and will be ignored in favor of the echo server.")
            continue

        if definition.server_type == "stdio":
            original_command = definition.command
            original_args = definition.args if definition.args is not None else []
            original_env = definition.env_vars if definition.env_vars is not None else {}
            if not original_command:
                logger.warning(f"[Config Builder] Skipping stdio definition '{definition.name}': command is missing."); continue

            # Initialize with default values
            command_to_use = original_command
            args_to_use = original_args

            # Windows adaptation logic
            if adapt_for_windows:
                command_basename_lower = os.path.basename(original_command).lower()

                if command_basename_lower == "npx":
                    command_to_use = "cmd"
                    # Form new arguments: /c npx -y [original_args]
                    args_to_use = ["/c", "npx"]
                    if "-y" not in original_args:
                        args_to_use.append("-y")
                    args_to_use.extend(original_args)
                    logger.debug(f"[Config Builder] Adapting '{original_command}' for Windows: 'cmd /c npx -y {' '.join(original_args)}' for server '{definition.name}'")

                elif command_basename_lower == "uvx":
                    command_to_use = "cmd"
                    # Form new arguments: /c uvx [original_args]
                    args_to_use = ["/c", "uvx"] + original_args
                    logger.debug(f"[Config Builder] Adapting '{original_command}' for Windows: 'cmd /c uvx {' '.join(original_args)}' for server '{definition.name}'")

                elif command_basename_lower == "docker":
                    command_to_use = "cmd"
                    # Form new arguments: /c docker run [original_args]
                    args_to_use = ["/c", "docker", "run"] + original_args
                    logger.debug(f"[Config Builder] Adapting '{original_command}' for Windows: 'cmd /c docker run {' '.join(original_args)}' for server '{definition.name}'")

            config_entry["command"] = command_to_use
            if args_to_use: config_entry["args"] = args_to_use # Use adapted or original args
            if original_env: config_entry["env"] = original_env

        elif definition.server_type in ["sse", "streamable_http"]:
            if not definition.url:
                logger.warning(f"[Config Builder] Skipping {definition.server_type} definition '{definition.name}': URL is missing."); continue
            config_entry["type"] = definition.server_type
            config_entry["url"] = definition.url
        else:
            logger.warning(f"[Config Builder] Skipping definition '{definition.name}': Unknown server type '{definition.server_type}'"); continue

        mcp_servers_config[definition.name] = config_entry

    # Add internal echo server for health check if enabled
    if settings.health_check_enabled:
        if settings.INTERNAL_ECHO_SERVER_NAME in mcp_servers_config:
            logger.warning(
                f"[Config Builder] Internal echo server name '{settings.INTERNAL_ECHO_SERVER_NAME}' is already used "
                f"by a user definition. Health check may not work correctly or may use "
                f"the user server instead of the built-in test server."
            )

        # Initialize echo server config
        echo_server_command = settings.INTERNAL_ECHO_SERVER_COMMAND
        echo_server_args = settings.INTERNAL_ECHO_SERVER_ARGS

        # Echo server adaptation for Windows
        if adapt_for_windows:
            echo_command_basename_lower = os.path.basename(echo_server_command).lower()
            if echo_command_basename_lower == "npx":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "npx"]
                if "-y" not in settings.INTERNAL_ECHO_SERVER_ARGS:
                    echo_server_args.append("-y")
                echo_server_args.extend(settings.INTERNAL_ECHO_SERVER_ARGS)
                logger.debug(f"[Config Builder] Adapting echo server command '{settings.INTERNAL_ECHO_SERVER_COMMAND}' for Windows")
            elif echo_command_basename_lower == "uvx":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "uvx"] + settings.INTERNAL_ECHO_SERVER_ARGS
                logger.debug(f"[Config Builder] Adapting echo server command '{settings.INTERNAL_ECHO_SERVER_COMMAND}' for Windows")
            elif echo_command_basename_lower == "docker":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "docker", "run"] + settings.INTERNAL_ECHO_SERVER_ARGS
                logger.debug(f"[Config Builder] Adapting echo server command '{settings.INTERNAL_ECHO_SERVER_COMMAND}' for Windows")

        echo_server_config = {
            "command": echo_server_command,
            "args": echo_server_args,
        }
        if settings.INTERNAL_ECHO_SERVER_ENV:
             echo_server_config["env"] = settings.INTERNAL_ECHO_SERVER_ENV

        mcp_servers_config[settings.INTERNAL_ECHO_SERVER_NAME] = echo_server_config
        logger.info(f"[Config Builder] Internal echo server '{settings.INTERNAL_ECHO_SERVER_NAME}' added to configuration (Windows adaptation: {'Yes' if adapt_for_windows else 'No'}).")

    return mcp_servers_config


def generate_mcpo_config_file(db: Session, settings: McpoSettings) -> bool:
    """
    Generates a standard MCPO configuration file and writes it to disk.
    Does not apply Windows adaptations.
    """
    output_path = settings.config_file_path
    logger.info(f"Generating standard MCPO configuration file: {output_path}")

    try:
        mcp_servers_config = _build_mcp_servers_config_dict(db, settings, adapt_for_windows=False)
        final_config = {"mcpServers": mcp_servers_config}

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_config, f, indent=2, ensure_ascii=False)
        logger.info(f"Standard MCPO configuration file successfully generated with {len(mcp_servers_config)} servers.")
        return True
    except Exception as e:
        logger.error(f"Error generating or writing standard MCPO configuration file to {output_path}: {e}", exc_info=True)
        return False

def generate_mcpo_config_content_for_windows(db: Session, settings: McpoSettings) -> str:
    """
    Generates MCPO configuration file content with Windows adaptations.
    Returns a JSON string.
    """
    logger.info(f"Generating MCPO configuration content for Windows (without writing file)...")
    try:
        mcp_servers_config = _build_mcp_servers_config_dict(db, settings, adapt_for_windows=True)
        final_config = {"mcpServers": mcp_servers_config}
        config_json_string = json.dumps(final_config, indent=2, ensure_ascii=False)
        logger.info(f"Windows configuration content successfully generated with {len(mcp_servers_config)} servers.")
        return config_json_string
    except Exception as e:
        logger.error(f"Error generating MCPO configuration content for Windows: {e}", exc_info=True)
        error_message = f"Error generating Windows config: {e}"
        # Return a JSON comment with the error
        return f"// {error_message}"
    




def _extract_servers_from_json(config_json_str: str) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """
    Helper to parse JSON (supporting multiple formats, including single object)
    and extract potential server entries. Handles duplicates within the input JSON.
    Returns a list of (name, data) tuples and a list of parsing/extraction errors.
    """
    servers_to_process: List[Tuple[str, Dict[str, Any]]] = []
    errors: List[str] = []
    processed_input_names: set[str] = set() # Track names found in this JSON

    try:
        data = json.loads(config_json_str)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON format: {str(e)}")
        return [], errors # Cannot proceed if JSON is invalid

    if isinstance(data, list):
        # --- Format 1: List of Objects ---
        logger.debug("Detected JSON list format for bulk add.")
        for index, item in enumerate(data):
            if isinstance(item, dict) and "name" in item:
                server_name = str(item.get("name", "")).strip()
                if not server_name:
                    errors.append(f"Entry at list index {index} has a missing or empty 'name' field.")
                    continue
                if server_name in processed_input_names:
                    errors.append(f"Duplicate name '{server_name}' found within the input JSON list. Only the first occurrence will be processed.")
                    continue
                processed_input_names.add(server_name)
                servers_to_process.append((server_name, item))
            else:
                errors.append(f"Element at list index {index} is not an object with a 'name' field.")
        if not servers_to_process and not errors:
             errors.append("The JSON list was empty or contained no valid server objects with names.")

    elif isinstance(data, dict):
        # --- Format 0: Single Object with 'name' key --- ADDED CHECK HERE ---
        if "name" in data:
             logger.debug("Detected single JSON object format with 'name' key.")
             server_name = str(data.get("name", "")).strip()
             if not server_name:
                 errors.append("The single JSON object has a missing or empty 'name' field.")
             else:
                 # Check if config_data_item part is actually a dict (it should be the whole 'data')
                 # In this case, the 'data' *is* the config_data_item
                 servers_to_process.append((server_name, data))
                 processed_input_names.add(server_name) # Track the name
        # --- End of Single Object Check ---

        # --- Format 2 or 3: Dictionary based (Only if not Format 0) ---
        elif "mcpServers" in data and isinstance(data["mcpServers"], dict):
            # Format 3: Object with "mcpServers" key
            logger.debug("Detected JSON object format with 'mcpServers' key.")
            target_dict = data["mcpServers"]
            if target_dict:
                for server_name, config_data_item in target_dict.items():
                    server_name = server_name.strip()
                    if not server_name:
                        errors.append("Found an entry in 'mcpServers' with an empty key (server name). Skipping.")
                        continue
                    if not isinstance(config_data_item, dict):
                        errors.append(f"Configuration for server '{server_name}' in 'mcpServers' is not an object. Skipping.")
                        continue
                    if server_name in processed_input_names:
                        errors.append(f"Duplicate name '{server_name}' found (first in object keys, then in mcpServers). Only the first occurrence will be processed.")
                        continue
                    processed_input_names.add(server_name)
                    servers_to_process.append((server_name, config_data_item))
            else:
                 errors.append("The 'mcpServers' object was present but empty.")

        elif not servers_to_process: # Only process as direct map if not single object and not mcpServers format found yet
            # Format 2: Direct mapping object
            logger.debug("Detected JSON direct mapping object format.")
            target_dict = data
            if target_dict:
                for server_name, config_data_item in target_dict.items():
                    server_name = server_name.strip()
                    if not server_name:
                        errors.append("Found an entry with an empty key (server name) in direct mapping. Skipping.")
                        continue
                    if not isinstance(config_data_item, dict):
                        errors.append(f"Configuration for server '{server_name}' in direct mapping is not an object. Skipping.")
                        continue
                    if server_name in processed_input_names:
                         # This case is unlikely now with the 'single object' check first, but keep for safety
                        errors.append(f"Duplicate name '{server_name}' found in direct mapping object. Only the first occurrence will be processed.")
                        continue
                    processed_input_names.add(server_name)
                    servers_to_process.append((server_name, config_data_item))
            else:
                 # Input was an empty dictionary
                 errors.append("The JSON object was empty.")

    else:
        # --- Unsupported Format ---
        errors.append("Unsupported JSON format. Expected an object or a list of objects.")

    # Final check if nothing was extracted despite no major errors
    if not servers_to_process and not errors:
        # This case should be rarer now, but handles scenarios like empty list/object inputs
        errors.append("No server entries could be extracted from the provided JSON structure.")

    logger.debug(f"JSON extraction finished. Found {len(servers_to_process)} potential servers. Errors: {len(errors)}")
    return servers_to_process, errors


# --- analyze_bulk_server_definitions function remains the same ---
# It correctly uses the output of the fixed _extract_servers_from_json
def analyze_bulk_server_definitions(
    db: Session,
    config_json_str: str,
    default_enabled: bool = False
) -> Tuple[AnalysisResult, List[str]]:
    # ... (function content unchanged as it relies on the extractor's output) ...
    logger.info("Analyzing bulk server definitions from JSON...")

    analysis: AnalysisResult = {"valid_new": [], "existing": [], "invalid": []}
    # Use the improved extraction function
    servers_to_process, parsing_errors = _extract_servers_from_json(config_json_str)

    # If initial parsing failed badly or found nothing, we might return early
    if not servers_to_process and parsing_errors:
        logger.warning(f"JSON parsing/extraction failed or found no servers. Errors: {parsing_errors}")
        return analysis, parsing_errors

    existing_db_names_stmt = select(ServerDefinition.name)
    existing_db_names = set(db.exec(existing_db_names_stmt).all())
    logger.debug(f"Found {len(existing_db_names)} existing server names in DB.")

    for server_name, config_data_item in servers_to_process:
        if server_name in existing_db_names:
            logger.debug(f"Server '{server_name}' already exists in DB. Categorizing as existing.")
            analysis["existing"].append(server_name)
            continue

        error_reason = None
        try:
            original_command = config_data_item.get("command")
            original_args = config_data_item.get("args", [])
            final_env = config_data_item.get("env", {}) # Map 'env'
            original_url = config_data_item.get("url")
            original_type = config_data_item.get("type")

            if not isinstance(original_args, list):
                 logger.warning(f"Server '{server_name}': 'args' field was not a list, correcting to empty list.")
                 original_args = []
            if not isinstance(final_env, dict):
                 logger.warning(f"Server '{server_name}': 'env' field was not a dict, correcting to empty dict.")
                 final_env = {}

            final_command, final_args = _deadapt_windows_command(original_command, original_args)
            final_url = original_url

            final_server_type = None
            if final_command:
                final_server_type = "stdio"
                final_url = None
            elif final_url:
                if original_type and original_type in ["sse", "streamable_http"]:
                    final_server_type = original_type
                else:
                    if original_type: logger.warning(f"Server '{server_name}': Invalid type '{original_type}' specified with URL, defaulting to 'sse'.")
                    else: logger.debug(f"Server '{server_name}': No type specified with URL, defaulting to 'sse'.")
                    final_server_type = "sse"
                final_command = None; final_args = []; final_env = {}
            else:
                 raise ValueError("Cannot determine type: 'command' or 'url' must be provided.")

            definition_to_validate = ServerDefinitionCreate(
                name=server_name,
                is_enabled=default_enabled,
                server_type=final_server_type,
                command=final_command,
                args=final_args,
                env_vars=final_env, # Mapped here
                url=final_url
            )
            analysis["valid_new"].append(definition_to_validate)
            logger.debug(f"Server '{server_name}' validated successfully as new.")

        except (ValueError, ValidationError) as e:
            error_reason = f"{e.__class__.__name__}: {str(e)}"
        except Exception as e:
            error_reason = f"Unexpected error: {e.__class__.__name__}: {str(e)}"
            logger.error(f"Unexpected error validating server '{server_name}': {e}", exc_info=True)

        if error_reason:
            logger.warning(f"Server '{server_name}' is invalid: {error_reason}")
            analysis["invalid"].append({
                "name": server_name,
                "data": config_data_item,
                "error": error_reason
            })

    logger.info(f"Bulk analysis completed: {len(analysis['valid_new'])} valid new, {len(analysis['existing'])} existing, {len(analysis['invalid'])} invalid.")
    return analysis, parsing_errors