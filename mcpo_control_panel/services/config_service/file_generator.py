# mcpo_control_panel/services/config_managers/file_generator.py
import json
import logging
import os
from typing import List, Optional, Dict, Any, TypedDict, Tuple
from pathlib import Path
from sqlmodel import Session
from pydantic import ValidationError

from ...models.server_definition import ServerDefinitionCreate, ServerDefinition # Import ServerDefinition for _build_mcp_servers_config_dict
from ...models.mcpo_settings import McpoSettings
from .definition_manager import get_server_definitions # Import from sibling module
from sqlmodel import select

logger = logging.getLogger(__name__)

class InvalidServerInfo(TypedDict):
    name: Optional[str]
    data: Dict[str, Any]
    error: str

class AnalysisResult(TypedDict):
    valid_new: List[ServerDefinitionCreate]
    existing: List[str]
    invalid: List[InvalidServerInfo]

def _get_data_dir() -> Path: # Helper specific to this module if needed for default paths
    return Path(os.getenv("MCPO_MANAGER_DATA_DIR_EFFECTIVE", Path.home() / ".mcpo_manager_data"))

def _build_mcp_servers_config_dict(db: Session, settings: McpoSettings, adapt_for_windows: bool = False) -> Dict[str, Any]:
    enabled_definitions = get_server_definitions(db, only_enabled=True, limit=10000)
    mcp_servers_config: Dict[str, Any] = {}

    for definition in enabled_definitions:
        config_entry: Dict[str, Any] = {}
        if definition.name == settings.INTERNAL_ECHO_SERVER_NAME and settings.health_check_enabled:
            logger.warning(f"[Config Builder] Server definition '{definition.name}' conflicts with internal echo server name and will be ignored.")
            continue

        if definition.server_type == "stdio":
            original_command = definition.command
            original_args = definition.args if definition.args is not None else []
            original_env = definition.env_vars if definition.env_vars is not None else {}
            if not original_command:
                logger.warning(f"[Config Builder] Skipping stdio definition '{definition.name}': command is missing."); continue
            
            command_to_use = original_command
            args_to_use = original_args

            if adapt_for_windows:
                command_basename_lower = os.path.basename(original_command).lower()
                if command_basename_lower == "npx":
                    command_to_use = "cmd"
                    args_to_use = ["/c", "npx"] + (["-y"] if "-y" not in original_args else []) + original_args
                elif command_basename_lower == "uvx":
                    command_to_use = "cmd"
                    args_to_use = ["/c", "uvx"] + original_args
                elif command_basename_lower == "docker":
                    command_to_use = "cmd"
                    args_to_use = ["/c", "docker", "run"] + original_args
            
            config_entry["command"] = command_to_use
            if args_to_use: config_entry["args"] = args_to_use
            if original_env: config_entry["env"] = original_env

        elif definition.server_type in ["sse", "streamable_http"]:
            if not definition.url:
                logger.warning(f"[Config Builder] Skipping {definition.server_type} definition '{definition.name}': URL is missing."); continue
            config_entry["type"] = definition.server_type
            config_entry["url"] = definition.url
        else:
            logger.warning(f"[Config Builder] Skipping definition '{definition.name}': Unknown server type '{definition.server_type}'"); continue
        mcp_servers_config[definition.name] = config_entry

    if settings.health_check_enabled:
        if settings.INTERNAL_ECHO_SERVER_NAME in mcp_servers_config:
            logger.warning(
                f"[Config Builder] Internal echo server name '{settings.INTERNAL_ECHO_SERVER_NAME}' is already used. Health check may conflict."
            )
        echo_server_command = settings.INTERNAL_ECHO_SERVER_COMMAND
        echo_server_args = list(settings.INTERNAL_ECHO_SERVER_ARGS) # Ensure it's a list

        if adapt_for_windows:
            echo_command_basename_lower = os.path.basename(echo_server_command).lower()
            if echo_command_basename_lower == "npx":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "npx"] + (["-y"] if "-y" not in settings.INTERNAL_ECHO_SERVER_ARGS else []) + settings.INTERNAL_ECHO_SERVER_ARGS
            elif echo_command_basename_lower == "uvx":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "uvx"] + settings.INTERNAL_ECHO_SERVER_ARGS
            elif echo_command_basename_lower == "docker":
                echo_server_command = "cmd"
                echo_server_args = ["/c", "docker", "run"] + settings.INTERNAL_ECHO_SERVER_ARGS
        
        echo_server_config = {"command": echo_server_command, "args": echo_server_args}
        if settings.INTERNAL_ECHO_SERVER_ENV:
             echo_server_config["env"] = settings.INTERNAL_ECHO_SERVER_ENV
        mcp_servers_config[settings.INTERNAL_ECHO_SERVER_NAME] = echo_server_config
        logger.info(f"[Config Builder] Internal echo server '{settings.INTERNAL_ECHO_SERVER_NAME}' added (Windows adapt: {'Yes' if adapt_for_windows else 'No'}).")
    return mcp_servers_config

def generate_mcpo_config_file(db: Session, settings: McpoSettings) -> bool:
    data_dir = _get_data_dir()
    config_filename = Path(settings.config_file_path).name
    if not config_filename:
        config_filename = "mcp_generated_config.json"
        logger.warning(f"Empty config_file_path in settings, defaulting to '{config_filename}'")
        # settings.config_file_path = config_filename # Avoid modifying settings object directly here

    output_path = data_dir / config_filename
    logger.info(f"Ensuring MCPO configuration file: {output_path}")

    # Manual mode handling
    if settings.manual_config_mode_enabled:
        if output_path.exists():
            logger.info(f"Manual config mode enabled. File '{output_path}' already exists. No changes made.")
            return True
        else:
            logger.info(f"Manual config mode enabled. File '{output_path}' does not exist. Creating with default empty content.")
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump({"mcpServers": {}}, f, indent=2, ensure_ascii=False)
                logger.info(f"Default empty manual MCPO configuration file created at {output_path}.")
                return True
            except Exception as e:
                logger.error(f"Error creating default empty manual MCPO config file at {output_path}: {e}", exc_info=True)
                return False
    
    # Automated mode: Generate from DB
    logger.info(f"Automated mode: Generating MCPO configuration file from database to {output_path}.")
    try:
        mcp_servers_config = _build_mcp_servers_config_dict(db, settings, adapt_for_windows=False)
        final_config = {"mcpServers": mcp_servers_config}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_config, f, indent=2, ensure_ascii=False)
        logger.info(f"MCPO configuration file successfully generated with {len(mcp_servers_config)} servers to {output_path}.")
        return True
    except Exception as e:
        logger.error(f"Error generating or writing MCPO configuration file to {output_path}: {e}", exc_info=True)
        return False

def generate_mcpo_config_content_for_windows(db: Session, settings: McpoSettings) -> str:
    # Note: This function is NOT called if manual_config_mode_enabled is true by the API endpoint.
    # The API endpoint handles serving the raw manual file with a warning.
    # So, this function can assume it's always in automated mode.
    logger.info(f"Generating MCPO configuration content for Windows (automated mode)...")
    try:
        mcp_servers_config = _build_mcp_servers_config_dict(db, settings, adapt_for_windows=True)
        final_config = {"mcpServers": mcp_servers_config}
        config_json_string = json.dumps(final_config, indent=2, ensure_ascii=False)
        logger.info(f"Windows configuration content generated with {len(mcp_servers_config)} servers.")
        return config_json_string
    except Exception as e:
        logger.error(f"Error generating MCPO configuration content for Windows: {e}", exc_info=True)
        return f"// Error generating Windows config: {e}"

def _deadapt_windows_command(command: Optional[str], args: List[str]) -> Tuple[Optional[str], List[str]]:
    if command == "cmd" and args and args[0].lower() == "/c" and len(args) > 1:
        executable = args[1].lower()
        if executable == "npx":
            args_start_index = 2
            if len(args) > 2 and args[2] == "-y": args_start_index = 3
            return "npx", args[args_start_index:]
        elif executable == "uvx":
            return "uvx", args[2:]
        elif executable == "docker":
            return "docker", args[3:] if len(args) > 2 and args[2].lower() == "run" else args[2:]
    return command, args

def _extract_servers_from_json(config_json_str: str) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    servers_to_process: List[Tuple[str, Dict[str, Any]]] = []
    errors: List[str] = []
    processed_input_names: set[str] = set()
    try:
        data = json.loads(config_json_str)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON format: {str(e)}"); return [], errors

    if isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, dict) and "name" in item:
                server_name = str(item.get("name", "")).strip()
                if not server_name: errors.append(f"Entry at index {index} missing 'name'."); continue
                if server_name in processed_input_names: errors.append(f"Duplicate name '{server_name}' in input list."); continue
                processed_input_names.add(server_name)
                servers_to_process.append((server_name, item))
            else: errors.append(f"Element at index {index} not an object with 'name'.")
        if not servers_to_process and not errors: errors.append("JSON list empty or no valid server objects.")
    elif isinstance(data, dict):
        if "name" in data: # Single object format
            server_name = str(data.get("name", "")).strip()
            if not server_name: errors.append("Single JSON object missing 'name'.")
            else: servers_to_process.append((server_name, data)); processed_input_names.add(server_name)
        elif "mcpServers" in data and isinstance(data["mcpServers"], dict): # mcpServers format
            target_dict = data["mcpServers"]
            if not target_dict: errors.append("'mcpServers' object empty.");
            for server_name, config_data_item in target_dict.items():
                server_name = server_name.strip()
                if not server_name: errors.append("Entry in 'mcpServers' with empty key."); continue
                if not isinstance(config_data_item, dict): errors.append(f"Config for '{server_name}' not an object."); continue
                if server_name in processed_input_names: errors.append(f"Duplicate name '{server_name}'."); continue
                processed_input_names.add(server_name); servers_to_process.append((server_name, config_data_item))
        elif not servers_to_process: # Direct mapping format
            target_dict = data
            if not target_dict: errors.append("JSON object was empty.");
            for server_name, config_data_item in target_dict.items():
                server_name = server_name.strip()
                if not server_name: errors.append("Entry with empty key in direct mapping."); continue
                if not isinstance(config_data_item, dict): errors.append(f"Config for '{server_name}' not an object."); continue
                if server_name in processed_input_names: errors.append(f"Duplicate name '{server_name}'."); continue
                processed_input_names.add(server_name); servers_to_process.append((server_name, config_data_item))
    else: errors.append("Unsupported JSON format. Expected object or list of objects.")
    if not servers_to_process and not errors: errors.append("No server entries extracted.")
    return servers_to_process, errors

def analyze_bulk_server_definitions(
    db: Session, config_json_str: str, default_enabled: bool = False
) -> Tuple[AnalysisResult, List[str]]:
    analysis: AnalysisResult = {"valid_new": [], "existing": [], "invalid": []}
    servers_to_process, parsing_errors = _extract_servers_from_json(config_json_str)
    if not servers_to_process and parsing_errors: return analysis, parsing_errors

    existing_db_names = set(db.exec(select(ServerDefinition.name)).all())
    for server_name, config_data_item in servers_to_process:
        if server_name in existing_db_names:
            analysis["existing"].append(server_name); continue
        error_reason = None
        try:
            original_command = config_data_item.get("command")
            original_args = config_data_item.get("args", [])
            final_env = config_data_item.get("env", {})
            original_url = config_data_item.get("url")
            original_type = config_data_item.get("type")

            if not isinstance(original_args, list): original_args = []
            if not isinstance(final_env, dict): final_env = {}

            final_command, final_args = _deadapt_windows_command(original_command, original_args)
            final_url = original_url
            final_server_type = None

            if final_command:
                final_server_type = "stdio"; final_url = None
            elif final_url:
                final_server_type = original_type if original_type in ["sse", "streamable_http"] else "sse"
                final_command = None; final_args = []; final_env = {}
            else: raise ValueError("Cannot determine type: 'command' or 'url' must be provided.")

            definition_to_validate = ServerDefinitionCreate(
                name=server_name, is_enabled=default_enabled, server_type=final_server_type,
                command=final_command, args=final_args, env_vars=final_env, url=final_url
            )
            analysis["valid_new"].append(definition_to_validate)
        except (ValueError, ValidationError) as e: error_reason = f"{e.__class__.__name__}: {str(e)}"
        except Exception as e: error_reason = f"Unexpected error: {e}"; logger.error(f"Validating '{server_name}': {e}", exc_info=True)
        if error_reason: analysis["invalid"].append({"name": server_name, "data": config_data_item, "error": error_reason})
    return analysis, parsing_errors