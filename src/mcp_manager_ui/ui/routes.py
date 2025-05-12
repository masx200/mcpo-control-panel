# ================================================
# FILE: src/mcp_manager_ui/ui/routes.py
# (Updated for Tabbed Add Page and 2-step Bulk Add)
# ================================================
import html
import logging
import json
import os
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote # For URL encoding server names

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError, BaseModel # Added BaseModel for payload
from sqlmodel import Session

# Removed get_mcpo_settings_dependency as it's not directly used in these routes anymore
# (used in settings routes, which are unchanged for now)

from ..db.database import get_session
from ..services import config_service, mcpo_service
from ..models.server_definition import (
    ServerDefinitionCreate, ServerDefinitionUpdate, ServerDefinitionRead
)
from ..models.mcpo_settings import McpoSettings # Still needed for logs page settings

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None # Set from main.py

# --- Helper function to de-adapt Windows commands ---
# (Function remains the same)
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
            # Adjusted to handle potential 'run' argument correctly
            new_command = "docker"
            if len(args) > 2 and args[2].lower() == "run":
                 new_args = args[3:] # Skip 'cmd /c docker run'
            else:
                 new_args = args[2:] # Skip 'cmd /c docker'
            logger.debug(f"De-adapting Windows: 'cmd /c docker...' -> '{new_command} {' '.join(new_args)}'")
            return new_command, new_args
    # If not a special case, return original command and args
    return command, args


# --- Main UI Pages ---

@router.get("/", response_class=HTMLResponse, name="ui_root")
async def get_index_page(
    request: Request,
    db: Session = Depends(get_session),
    # Query parameters for success/error messages after redirects
    single_add_success: Optional[str] = None,
    bulk_success: Optional[str] = None, # Can be count or message
    update_success: Optional[str] = None,
    bulk_error: Optional[str] = None,
    bulk_info: Optional[str] = None
    ):
    """Displays the main page with the server list and MCPO controls."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    server_definitions = config_service.get_server_definitions(db)
    definitions_read = [ServerDefinitionRead.model_validate(d) for d in server_definitions]
    current_mcpo_status = mcpo_service.get_mcpo_status()
    mcpo_settings = config_service.load_mcpo_settings()

    return templates.TemplateResponse(
        "index.html", {
            "request": request,
            "server_definitions": definitions_read,
            "mcpo_status": current_mcpo_status,
            "mcpo_settings": mcpo_settings,
            # Pass toast data to the template (if present)
            "single_add_success_msg": single_add_success,
            "bulk_success_msg": bulk_success,
            "update_success_msg": update_success,
            "bulk_error_msg": bulk_error,
            "bulk_info_msg": bulk_info,
        })

@router.get("/tools", response_class=HTMLResponse, name="ui_tools")
async def get_tools_page(request: Request, db: Session = Depends(get_session)):
    """Displays the page with available tools from the running MCPO."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    tools_data: Dict[str, Any] = {}
    error_message: Optional[str] = None

    try:
        # This function now returns a dictionary including 'base_url_for_links'
        tools_data = await mcpo_service.get_aggregated_tools_from_mcpo(db)
    except Exception as e:
        logger.error(f"Error getting aggregated tool data: {e}", exc_info=True)
        error_message = "An error occurred while retrieving tool information."
        # Set default value if an error occurred
        tools_data = {"status": "ERROR", "servers": {}, "base_url_for_links": f"http://127.0.0.1:{config_service.load_mcpo_settings().port}"}

    return templates.TemplateResponse(
        "tools.html", {
            "request": request,
            "tools_data": tools_data, # Contains status, servers, and base_url_for_links
            "error_message": error_message
        })

# --- Get MCPO settings dependency ---
# Moved here as it's used by logs page now
def get_mcpo_settings_dependency() -> McpoSettings:
     return config_service.load_mcpo_settings()

@router.get("/logs", response_class=HTMLResponse, name="ui_logs")
async def show_logs_page(
    request: Request,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency) # Use dependency here
):
    """
    Displays the logs page. Logs themselves will be loaded via HTMX.
    """
    logger.debug("UI Request: GET /logs page")
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    # Check if the log file exists to show status in the template
    log_file_path_exists = False
    if settings.log_file_path and os.path.exists(settings.log_file_path):
        log_file_path_exists = True
        logger.debug(f"Log file '{settings.log_file_path}' exists.")
    elif settings.log_file_path:
        logger.warning(f"Log file path configured ('{settings.log_file_path}') but file does not exist.")
    else:
        logger.info("Log file path is not configured.")

    # Pass settings and file existence status to the template
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "mcpo_settings": settings, # Settings are needed for path and refresh interval
        "log_file_path_exists": log_file_path_exists # File existence flag
    })


# --- Editing a Single Server ---
# (get_edit_server_form and handle_update_server_form remain unchanged)
@router.get("/servers/{server_id}/edit", response_class=HTMLResponse, name="ui_edit_server_form")
async def get_edit_server_form(request: Request, server_id: int, db: Session = Depends(get_session)):
    """Displays the server definition edit page."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    definition_db = config_service.get_server_definition(db, server_id)
    if not definition_db:
        raise HTTPException(status_code=404, detail="Server definition not found")

    definition_data = ServerDefinitionRead.model_validate(definition_db).model_dump()
    action_url = request.url_for("ui_update_server", server_id=server_id)
    form_title = f"Editing '{definition_data.get('name', '')}'"
    submit_button_text = "Update Definition"

    return templates.TemplateResponse("edit_server_page.html", {
        "request": request,
        "action_url": action_url,
        "submit_button_text": submit_button_text,
        "server_data": definition_data,
        "form_title": form_title,
        "is_add_form": False,
        "cancel_url": request.url_for("ui_root")
    })

@router.post("/servers/{server_id}/edit", name="ui_update_server")
async def handle_update_server_form(
    request: Request, server_id: int, db: Session = Depends(get_session),
    # Parameters from the form
    name: str = Form(...),
    server_type: str = Form(...),
    is_enabled: bool = Form(False),
    command: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"), # Dynamic argument fields
    env_keys: List[str] = Form([], alias="env_key[]"),   # Dynamic environment variable fields (keys)
    env_values: List[str] = Form([], alias="env_value[]") # Dynamic environment variable fields (values)
):
    """Handles data from the server definition edit form."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info(f"UI Request: POST /servers/{server_id}/edit (Updating server)")

    # Process and validate form data
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip():
                processed_env_vars[key_stripped] = value.strip()
    else:
        logger.warning(f"Mismatch in the number of env_keys and env_values when updating server ID {server_id}")

    current_command = command if command and command.strip() else None
    current_url = url if url and url.strip() else None
    final_args = processed_args
    final_env_vars = processed_env_vars
    error_msg: Optional[str] = None

    # De-adapt command if needed (do this *before* clearing based on type)
    current_command, final_args = _deadapt_windows_command(current_command, final_args)

    # Clear fields depending on server type
    if server_type == 'stdio':
        current_url = None
    elif server_type in ['sse', 'streamable_http']:
        current_command = None
        final_args = []
        final_env_vars = {}
    else:
        error_msg = "Unknown server type."

    # Check mandatory fields for the type
    if not error_msg:
        if server_type == 'stdio' and not current_command:
            error_msg = "The 'Command' field is mandatory for type 'stdio'."
        elif server_type in ['sse', 'streamable_http'] and not current_url:
            error_msg = f"The 'URL' field is mandatory for type '{server_type}'."

    # Data for re-rendering the form in case of error
    form_data_on_error = {
        "id": server_id,
        "name": name,
        "server_type": server_type,
        "is_enabled": is_enabled,
        "command": current_command,
        "args": final_args,
        "env_vars": final_env_vars,
        "url": current_url
    }
    action_url = request.url_for("ui_update_server", server_id=server_id)
    form_title = f"Editing '{name}' (Error)"
    submit_button_text = "Update Definition"

    # If there's a validation error, re-render the form with the error
    if error_msg:
        return templates.TemplateResponse("edit_server_page.html", {
            "request": request,
            "action_url": action_url,
            "submit_button_text": submit_button_text,
            "server_data": form_data_on_error,
            "form_title": form_title,
            "is_add_form": False,
            "error": error_msg,
            "cancel_url": request.url_for("ui_root")
            }, status_code=400)

    # Attempt to update data in the DB
    try:
        definition_in = ServerDefinitionUpdate(
            name=name,
            server_type=server_type,
            is_enabled=is_enabled,
            command=current_command,
            args=final_args,
            env_vars=final_env_vars,
            url=current_url
        )
        updated = config_service.update_server_definition(db=db, server_id=server_id, definition_in=definition_in)

        if not updated:
            # Server not found during update
            return templates.TemplateResponse("edit_server_page.html", {
                "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
                "server_data": form_data_on_error, "form_title": f"Editing '{name}' (Not Found)",
                "is_add_form": False, "error": "Server definition not found for update.",
                "cancel_url": request.url_for("ui_root")
                }, status_code=404)

        # Successful update -> Redirect to main page with server name
        logger.info(f"Server definition ID {server_id} successfully updated.")
        redirect_url = str(request.url_for("ui_root")) + f"?update_success={quote(updated.name)}"
        return RedirectResponse(url=redirect_url, status_code=303) # PRG pattern

    except (ValueError, ValidationError) as e:
        # Business logic error (e.g., duplicate name) or Pydantic validation
        error_text = f"Failed to update: {str(e)}"
        logger.warning(f"Error updating server ID {server_id}: {error_text}")
        return templates.TemplateResponse("edit_server_page.html", {
            "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
            "server_data": form_data_on_error, "form_title": form_title,
            "is_add_form": False, "error": error_text,
            "cancel_url": request.url_for("ui_root")
            }, status_code=400)
    except Exception as e:
        # Unexpected server error
        logger.error(f"Unexpected error updating server ID {server_id}: {e}", exc_info=True)
        return templates.TemplateResponse("edit_server_page.html", {
             "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
             "server_data": form_data_on_error, "form_title": f"Editing '{name}' (Server Error)",
             "is_add_form": False, "error": "Unexpected server error.",
             "cancel_url": request.url_for("ui_root")
             }, status_code=500)


# --- MCPO Settings ---
# (get_mcpo_settings_form and handle_update_mcpo_settings_form remain unchanged)
@router.get("/settings", response_class=HTMLResponse, name="ui_edit_mcpo_settings_form")
async def get_mcpo_settings_form(request: Request):
    """Displays the page with the MCPO settings form."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    settings = config_service.load_mcpo_settings()
    return templates.TemplateResponse("mcpo_settings_form.html", {
        "request": request,
        "settings": settings.model_dump(), # Pass dictionary for the form
        "error": None,
        "success": None
    })

@router.post("/settings", name="ui_update_mcpo_settings")
async def handle_update_mcpo_settings_form(
    request: Request,
    # Parameters from the MCPO settings form
    port: int = Form(...),
    public_base_url: Optional[str] = Form(None),
    api_key: Optional[str] = Form(None),
    use_api_key: bool = Form(False),
    config_file_path: str = Form(...),
    log_file_path: Optional[str] = Form(None),
    log_auto_refresh_enabled: bool = Form(False),
    log_auto_refresh_interval_seconds: int = Form(...),
    health_check_enabled: bool = Form(False),
    health_check_interval_seconds: int = Form(...),
    health_check_failure_attempts: int = Form(...),
    health_check_failure_retry_delay_seconds: int = Form(...),
    auto_restart_on_failure: bool = Form(False)
):
    """Handles data from the MCPO settings form."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info("UI Request: POST /settings (Updating all MCPO settings)")

    error_msg: Optional[str] = None
    success_msg: Optional[str] = None

    # Collect form data for display (even if validation fails)
    form_data_to_display = {
        "port": port,
        "public_base_url": public_base_url,
        "api_key": api_key,
        "use_api_key": use_api_key,
        "config_file_path": config_file_path,
        "log_file_path": log_file_path,
        "log_auto_refresh_enabled": log_auto_refresh_enabled,
        "log_auto_refresh_interval_seconds": log_auto_refresh_interval_seconds,
        "health_check_enabled": health_check_enabled,
        "health_check_interval_seconds": health_check_interval_seconds,
        "health_check_failure_attempts": health_check_failure_attempts,
        "health_check_failure_retry_delay_seconds": health_check_failure_retry_delay_seconds,
        "auto_restart_on_failure": auto_restart_on_failure
    }

    try:
        # Clean optional string fields
        clean_api_key = api_key if api_key and api_key.strip() else None
        clean_log_file_path = log_file_path if log_file_path and log_file_path.strip() else None
        clean_public_base_url = public_base_url if public_base_url and public_base_url.strip() else None

        # Create model instance for validation
        settings_for_validation = McpoSettings(
            port=port,
            public_base_url=clean_public_base_url, # Use cleaned value
            api_key=clean_api_key,
            use_api_key=use_api_key,
            config_file_path=config_file_path,
            log_file_path=clean_log_file_path,
            log_auto_refresh_enabled=log_auto_refresh_enabled,
            log_auto_refresh_interval_seconds=log_auto_refresh_interval_seconds,
            health_check_enabled=health_check_enabled,
            health_check_interval_seconds=health_check_interval_seconds,
            health_check_failure_attempts=health_check_failure_attempts,
            health_check_failure_retry_delay_seconds=health_check_failure_retry_delay_seconds,
            auto_restart_on_failure=auto_restart_on_failure
        )

        # Save valid settings via service
        if config_service.save_mcpo_settings(settings_for_validation):
            success_msg = "MCPO settings successfully updated."
            logger.info(success_msg)
            # Update data for display with validated values
            form_data_to_display = settings_for_validation.model_dump()
        else:
            error_msg = "Failed to save MCPO settings."
            logger.error(error_msg) # Log error if save fails

    except ValidationError as ve:
        # Pydantic validation error
        logger.warning(f"Validation error when updating MCPO settings: {ve.errors(include_url=False)}")
        error_details = [
            f"Field '{'.'.join(map(str, e['loc'])) if e['loc'] else 'field'}': {e['msg']}"
            for e in ve.errors(include_url=False)
        ]
        error_msg = "Validation errors: " + "; ".join(error_details)
    except Exception as e:
        # Unexpected error
        logger.error(f"Unexpected error updating settings: {e}", exc_info=True)
        error_msg = f"An unexpected error occurred: {str(e)}"

    # Render the same page with the result
    return templates.TemplateResponse(
        "mcpo_settings_form.html",
        {"request": request, "settings": form_data_to_display, "error": error_msg, "success": success_msg}
    )


# --- Server Addition Routes (New Structure) ---

@router.get("/servers/add", response_class=HTMLResponse, name="ui_add_servers_form")
async def get_add_servers_page(
    request: Request,
    # Parameters for re-rendering the single-add form in case of POST error
    single_server_error: Optional[str] = None,
    single_server_form_data: Optional[dict] = None # Expects a dict matching form fields
):
    """
    Displays the redesigned page for adding single or bulk servers using tabs.
    Replaces the old /servers/bulk_add route.
    """
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")

    single_add_action_url = request.url_for("ui_add_single_server") # Action for the single form
    bulk_analyze_action_url = request.url_for("ui_analyze_bulk_servers") # Action for bulk analyze

    # Note: We no longer pass bulk_* variables here, as bulk handling is done via HTMX partials
    return templates.TemplateResponse("add_servers_page.html", {
        "request": request,
        "single_add_action_url": single_add_action_url,
        "bulk_analyze_action_url": bulk_analyze_action_url,
        # Pass data back to single form only if there was an error during its submission
        "single_server_form_data": single_server_form_data or {}, # Ensure it's a dict
        "single_server_error": single_server_error,
    })

@router.post("/servers/add_single", name="ui_add_single_server")
async def handle_add_single_server_form(
    request: Request, db: Session = Depends(get_session),
    # Parameters from the single addition form (inside the tab)
    name: str = Form(...),
    server_type: str = Form(...),
    is_enabled: bool = Form(False),
    command: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"),
    env_keys: List[str] = Form([], alias="env_key[]"),
    env_values: List[str] = Form([], alias="env_value[]")
):
    """Handles data from the single server addition form submitted from the 'add_servers_page.html'."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    logger.info(f"UI Request: POST /servers/add_single (Adding server '{name}')")

    # --- Data processing and validation (similar to update form) ---
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip():
                processed_env_vars[key_stripped] = value.strip()
    else:
        logger.warning("Mismatch in the number of env_keys and env_values when adding server")

    final_command = command if command and command.strip() else None
    final_args = processed_args
    final_env_vars = processed_env_vars
    final_url = url if url and url.strip() else None
    error_msg: Optional[str] = None

    # De-adapt command if needed
    final_command, final_args = _deadapt_windows_command(final_command, final_args)

    # Clear fields based on type and check mandatory fields
    if server_type == 'stdio':
        final_url = None
        if not final_command: error_msg = "The 'Command' field is mandatory for type 'stdio'."
    elif server_type in ['sse', 'streamable_http']:
        final_command = None; final_args = []; final_env_vars = {}
        if not final_url: error_msg = f"The 'URL' field is mandatory for type '{server_type}'."
    else:
        error_msg = "Unknown server type."

    # Prepare form data for potential re-rendering on error
    form_data_on_error = {
        "name": name, "server_type": server_type, "is_enabled": is_enabled,
        "command": final_command, "args": final_args,
        "env_vars": final_env_vars, "url": final_url
    }
    # --- End Data processing ---

    # If validation error, re-render the NEW add_servers_page.html with error context
    if error_msg:
        single_add_action_url = request.url_for("ui_add_single_server")
        bulk_analyze_action_url = request.url_for("ui_analyze_bulk_servers")
        logger.warning(f"Validation error adding server '{name}': {error_msg}")
        return templates.TemplateResponse("add_servers_page.html", {
            "request": request,
            "single_add_action_url": single_add_action_url,
            "bulk_analyze_action_url": bulk_analyze_action_url,
            "single_server_form_data": form_data_on_error, # Pass data back to form
            "single_server_error": error_msg, # Show error in single add section
            # 'bulk_preview_content' is not needed here as bulk state is separate
            }, status_code=400)

    # Attempt to create server definition in DB
    try:
        definition_in = ServerDefinitionCreate(
            name=name, server_type=server_type, is_enabled=is_enabled,
            command=final_command, args=final_args, env_vars=final_env_vars, url=final_url
        )
        created = config_service.create_server_definition(db=db, definition_in=definition_in)

        # Successful addition -> Redirect to main page with success message
        logger.info(f"Successfully added server definition '{created.name}' (ID: {created.id}).")
        redirect_url = str(request.url_for("ui_root")) + f"?single_add_success={quote(created.name)}"
        return RedirectResponse(url=redirect_url, status_code=303) # PRG pattern

    except (ValueError, ValidationError) as e:
        # DB or validation error during creation (e.g., duplicate name)
        error_text = f"Failed to add server: {str(e)}"
        logger.warning(f"Error adding server '{name}': {error_text}")
        # Re-render the add_servers_page.html with the error message in the single add tab
        single_add_action_url = request.url_for("ui_add_single_server")
        bulk_analyze_action_url = request.url_for("ui_analyze_bulk_servers")
        return templates.TemplateResponse("add_servers_page.html", {
            "request": request,
            "single_add_action_url": single_add_action_url,
            "bulk_analyze_action_url": bulk_analyze_action_url,
            "single_server_form_data": form_data_on_error,
            "single_server_error": error_text, # Show error in single add section
            }, status_code=400)
    except Exception as e:
        # Unexpected error
        logger.error(f"Unexpected error adding server '{name}': {e}", exc_info=True)
        # Re-render the add_servers_page.html with a generic error
        single_add_action_url = request.url_for("ui_add_single_server")
        bulk_analyze_action_url = request.url_for("ui_analyze_bulk_servers")
        return templates.TemplateResponse("add_servers_page.html", {
             "request": request,
             "single_add_action_url": single_add_action_url,
             "bulk_analyze_action_url": bulk_analyze_action_url,
             "single_server_form_data": form_data_on_error,
             "single_server_error": "Unexpected server error during addition.",
             }, status_code=500)


@router.post("/servers/analyze-bulk", response_class=HTMLResponse, name="ui_analyze_bulk_servers")
async def handle_analyze_bulk_servers(
    request: Request, db: Session = Depends(get_session),
    config_json_str: str = Form(..., alias="configJsonStr"),
    default_enabled: bool = Form(False) # Get the 'enable' checkbox state
):
    """
    Analyzes the provided JSON and returns an HTML fragment (_bulk_add_preview.html)
    with the preview of servers to be added, existing ones, and invalid ones.
    Does NOT modify the database. Triggered via HTMX from the bulk add tab.
    """
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for bulk analysis")
    logger.info("UI Request: POST /servers/analyze-bulk (Analyzing JSON for bulk add)")

    # Call the service function to analyze the JSON content
    # This function now handles different JSON formats and Windows command de-adaptation
    analysis_result, parsing_errors = config_service.analyze_bulk_server_definitions(
        db=db,
        config_json_str=config_json_str,
        default_enabled=default_enabled # Pass the desired enabled state
    )

    # Serialize the list of *valid new* server definitions for the confirmation step
    # Store the validated Pydantic models as JSON string in a hidden input
    serialized_valid_servers = "[]" # Default to empty JSON array
    if analysis_result["valid_new"]:
        try:
            # Use model_dump for Pydantic v2+ to get JSON-serializable dicts
            valid_new_dicts = [server.model_dump(mode='json') for server in analysis_result["valid_new"]]
            serialized_valid_servers = json.dumps(valid_new_dicts)
            logger.debug(f"Serialized {len(valid_new_dicts)} valid servers for confirmation step.")
        except Exception as e:
            logger.error(f"Error serializing valid server data for confirmation: {e}", exc_info=True)
            # If serialization fails, add error and clear the list to prevent confirmation
            parsing_errors.append("Internal error: Failed to prepare valid data for confirmation.")
            analysis_result["valid_new"] = []

    # Render the partial template (_bulk_add_preview.html) with the analysis results
    return templates.TemplateResponse("_bulk_add_preview.html", {
        "request": request,
        "analysis": analysis_result, # Contains 'valid_new', 'existing', 'invalid' lists
        "parsing_errors": parsing_errors, # List of initial JSON parsing/extraction errors
        "serialized_valid_servers": serialized_valid_servers, # Pass JSON string to hidden input
        "confirm_action_url": request.url_for("ui_confirm_bulk_add") # URL for the confirm button's form
    })


@router.post("/servers/confirm-bulk-add", name="ui_confirm_bulk_add")
async def handle_confirm_bulk_add(
    request: Request,
    db: Session = Depends(get_session),
    # Get the serialized JSON string from the hidden form field
    valid_new_servers_json: str = Form(...)
):
    """
    Receives the serialized list of validated server definitions FROM THE FORM POST
    (originating from the hidden input in _bulk_add_preview.html).
    Adds these servers to the database. Redirects to the index page with results.
    """
    if not templates:
         # Good practice even if redirecting
        raise HTTPException(status_code=500, detail="Templates not configured")

    logger.info(f"UI Request: POST /servers/confirm-bulk-add (Confirming bulk add of servers)")

    added_count = 0
    errors: List[str] = [] # List to store specific errors encountered during this final add step

    try:
        # Deserialize the JSON string back into a list of dictionaries
        servers_to_add_data = json.loads(valid_new_servers_json)

        # Basic validation of the received data structure
        if not isinstance(servers_to_add_data, list):
            raise ValueError("Invalid payload format: Expected a list of server definitions.")

        if not servers_to_add_data:
             logger.warning("Confirmation called, but the list of servers to add was empty.")
             # Redirect with an info message if the list was empty
             redirect_url = str(request.url_for("ui_root")) + "?bulk_info=No new servers were available to add."
             return RedirectResponse(url=redirect_url, status_code=303)

        logger.info(f"Attempting to add {len(servers_to_add_data)} servers from confirmed list.")

        # Loop through the data and attempt to create each server definition
        for server_data in servers_to_add_data:
            server_name = server_data.get("name", "Unknown") # For logging/error messages
            try:
                # Re-validate with Pydantic model before creating
                # This ensures data integrity and catches potential issues
                definition_in = ServerDefinitionCreate(**server_data)

                # Call the service function to create the definition in the DB
                # This service function contains the check for duplicate names
                config_service.create_server_definition(db=db, definition_in=definition_in)
                added_count += 1
                # logger.debug(f"Successfully added confirmed server '{server_name}'.")

            except (ValidationError, ValueError) as e:
                 # Handle validation errors (e.g., duplicate name check in create_server_definition)
                 msg = f"Error adding '{server_name}' during confirmation: {str(e)}"
                 errors.append(msg)
                 logger.warning(msg)
                 # Continue processing other servers
            except Exception as e:
                 # Handle unexpected errors during database operation
                 msg = f"Unexpected error adding '{server_name}' during confirmation: {str(e)}"
                 errors.append(msg)
                 logger.error(msg, exc_info=True)
                 # Continue processing other servers

    except json.JSONDecodeError as e:
        # Error if the hidden input contained invalid JSON
        errors.append(f"Failed to parse server data for confirmation: {e}")
        logger.error(f"JSON decode error during bulk confirmation: {e}")
    except Exception as e:
        # Catch any other unexpected errors during the process
        errors.append(f"Unexpected error during confirmation process: {e}")
        logger.error(f"Unexpected error during bulk confirmation: {e}", exc_info=True)

    # --- Prepare Redirect URL with Status Parameters for Toasts ---
    redirect_url = str(request.url_for("ui_root"))
    query_params = {}

    if added_count > 0:
        query_params["bulk_success"] = str(added_count)
        logger.info(f"Bulk confirmation complete. Successfully added {added_count} servers.")
        if errors:
            # If some succeeded but others failed, add error info too
            error_summary = f"Added {added_count} servers, but encountered {len(errors)} errors (see logs)."
            query_params["bulk_error"] = error_summary # Keep it concise for URL
            logger.error(f"Bulk confirmation finished with {len(errors)} errors: {'; '.join(errors)}")
    elif errors:
         # If no servers were added and there were errors
         logger.error(f"Bulk confirmation failed. Errors: {'; '.join(errors)}")
         error_summary = f"Failed to add servers during confirmation (Errors: {len(errors)} - see logs)."
         query_params["bulk_error"] = error_summary
    else:
         # Case: Confirmation called, nothing added, no errors (e.g., empty initial list - handled earlier, but safe)
         logger.warning("Bulk confirmation called, but no servers were added and no errors recorded.")
         # query_params["bulk_info"] = "No new servers were added during confirmation." # Already handled

    # Append query parameters to the redirect URL, safely encoding values
    if query_params:
        encoded_params = "&".join(f"{k}={quote(str(v))}" for k, v in query_params.items())
        redirect_url += f"?{encoded_params}"

    # Perform the redirect using the Post-Redirect-Get (PRG) pattern
    return RedirectResponse(url=redirect_url, status_code=303) # 303 See Other


# --- Remove old Bulk Add Routes ---
# DELETE or comment out:
# @router.get("/servers/bulk_add", ...)
# @router.post("/servers/bulk_add", ...)
# async def get_bulk_add_form(...)
# async def handle_bulk_add_form(...)