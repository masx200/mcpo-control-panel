# mcpo_control_panel/ui/routers/main_ui_routes.py
import html
import logging
import json
import os
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session

from ...db.database import get_session
from ...services import config_service, mcpo_service
from ...models.server_definition import (
    ServerDefinitionCreate, ServerDefinitionUpdate, ServerDefinitionRead
)
from ...models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None 

def set_templates_for_main_ui_routes(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates

def _deadapt_windows_command(command: Optional[str], args: List[str]) -> Tuple[Optional[str], List[str]]:
    if command == "cmd" and args and args[0].lower() == "/c" and len(args) > 1:
        executable = args[1].lower()
        if executable == "npx":
            args_start_index = 2
            if len(args) > 2 and args[2] == "-y":
                args_start_index = 3
            new_command = "npx"
            new_args = args[args_start_index:]
            return new_command, new_args
        elif executable == "uvx":
            new_command = "uvx"
            new_args = args[2:]
            return new_command, new_args
        elif executable == "docker":
            new_command = "docker"
            if len(args) > 2 and args[2].lower() == "run":
                 new_args = args[3:]
            else:
                 new_args = args[2:]
            return new_command, new_args
    return command, args

@router.get("/", response_class=HTMLResponse, name="ui_root")
async def get_index_page(
    request: Request,
    db: Session = Depends(get_session),
    single_add_success: Optional[str] = None,
    bulk_success: Optional[str] = None,
    update_success: Optional[str] = None,
    bulk_error: Optional[str] = None,
    bulk_info: Optional[str] = None
    ):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")

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
            "single_add_success_msg": single_add_success,
            "bulk_success_msg": bulk_success,
            "update_success_msg": update_success,
            "bulk_error_msg": bulk_error,
            "bulk_info_msg": bulk_info,
        })

@router.get("/tools", response_class=HTMLResponse, name="ui_tools")
async def get_tools_page(request: Request, db: Session = Depends(get_session)):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
    
    mcpo_settings = config_service.load_mcpo_settings() # Load settings
    tools_data: Dict[str, Any] = {}
    error_message: Optional[str] = None
    
    try:
        tools_data = await mcpo_service.get_aggregated_tools_from_mcpo(db)
    except Exception as e:
        logger.error(f"Error getting aggregated tool data: {e}", exc_info=True)
        error_message = "An error occurred while retrieving tool information."
        # Ensure mcpo_settings is available for the fallback URL
        tools_data = {"status": "ERROR", "servers": {}, "base_url_for_links": f"http://127.0.0.1:{mcpo_settings.port}"}

    return templates.TemplateResponse(
        "tools.html", {
            "request": request,
            "tools_data": tools_data,
            "error_message": error_message,
            "mcpo_settings": mcpo_settings # Pass settings for base.html
        })

def get_mcpo_settings_dependency_for_logs() -> McpoSettings:
     return config_service.load_mcpo_settings()

@router.get("/logs", response_class=HTMLResponse, name="ui_logs")
async def show_logs_page(
    request: Request,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency_for_logs) # This already provides mcpo_settings
):
    logger.debug("UI Request: GET /logs page")
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")

    log_file_path_exists = False
    if settings.log_file_path and os.path.exists(settings.log_file_path):
        log_file_path_exists = True
    elif settings.log_file_path:
        logger.warning(f"Log file path configured ('{settings.log_file_path}') but file does not exist.")
    else:
        logger.info("Log file path is not configured.")

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "mcpo_settings": settings, # settings is already mcpo_settings here
        "log_file_path_exists": log_file_path_exists
    })

@router.get("/servers/{server_id}/edit", response_class=HTMLResponse, name="ui_edit_server_form")
async def get_edit_server_form(request: Request, server_id: int, db: Session = Depends(get_session)):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
    
    mcpo_settings = config_service.load_mcpo_settings() # Load settings
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
        "cancel_url": request.url_for("ui_root"),
        "mcpo_settings": mcpo_settings # Pass settings for base.html
    })

@router.post("/servers/{server_id}/edit", name="ui_update_server")
async def handle_update_server_form(
    request: Request, server_id: int, db: Session = Depends(get_session),
    name: str = Form(...),
    server_type: str = Form(...),
    is_enabled: bool = Form(False),
    command: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"),
    env_keys: List[str] = Form([], alias="env_key[]"),
    env_values: List[str] = Form([], alias="env_value[]")
):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
    
    mcpo_settings = config_service.load_mcpo_settings() # Load settings for potential error re-render
    logger.info(f"UI Request: POST /servers/{server_id}/edit (Updating server)")
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip():
                processed_env_vars[key_stripped] = value.strip()
    else:
        logger.warning(f"Mismatch in env_keys and env_values updating server ID {server_id}")

    current_command = command if command and command.strip() else None
    current_url = url if url and url.strip() else None
    final_args = processed_args
    final_env_vars = processed_env_vars
    error_msg: Optional[str] = None
    current_command, final_args = _deadapt_windows_command(current_command, final_args)

    if server_type == 'stdio':
        current_url = None
    elif server_type in ['sse', 'streamable_http']:
        current_command = None
        final_args = []
        final_env_vars = {}
    else:
        error_msg = "Unknown server type."

    if not error_msg:
        if server_type == 'stdio' and not current_command:
            error_msg = "The 'Command' field is mandatory for type 'stdio'."
        elif server_type in ['sse', 'streamable_http'] and not current_url:
            error_msg = f"The 'URL' field is mandatory for type '{server_type}'."

    form_data_on_error = {
        "id": server_id, "name": name, "server_type": server_type, "is_enabled": is_enabled,
        "command": current_command, "args": final_args, "env_vars": final_env_vars, "url": current_url
    }
    action_url = request.url_for("ui_update_server", server_id=server_id)
    form_title = f"Editing '{name}' (Error)"
    submit_button_text = "Update Definition"

    if error_msg:
        return templates.TemplateResponse("edit_server_page.html", {
            "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
            "server_data": form_data_on_error, "form_title": form_title, "is_add_form": False,
            "error": error_msg, "cancel_url": request.url_for("ui_root"),
            "mcpo_settings": mcpo_settings # Pass settings
            }, status_code=400)
    try:
        definition_in = ServerDefinitionUpdate(
            name=name, server_type=server_type, is_enabled=is_enabled, command=current_command,
            args=final_args, env_vars=final_env_vars, url=current_url
        )
        updated = config_service.update_server_definition(db=db, server_id=server_id, definition_in=definition_in)
        if not updated:
            return templates.TemplateResponse("edit_server_page.html", {
                "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
                "server_data": form_data_on_error, "form_title": f"Editing '{name}' (Not Found)",
                "is_add_form": False, "error": "Server definition not found for update.",
                "cancel_url": request.url_for("ui_root"),
                "mcpo_settings": mcpo_settings # Pass settings
                }, status_code=404)
        redirect_url = str(request.url_for("ui_root")) + f"?update_success={quote(updated.name)}"
        return RedirectResponse(url=redirect_url, status_code=303)
    except (ValueError, ValidationError) as e:
        error_text = f"Failed to update: {str(e)}"
        return templates.TemplateResponse("edit_server_page.html", {
            "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
            "server_data": form_data_on_error, "form_title": form_title, "is_add_form": False,
            "error": error_text, "cancel_url": request.url_for("ui_root"),
            "mcpo_settings": mcpo_settings # Pass settings
            }, status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error updating server ID {server_id}: {e}", exc_info=True)
        return templates.TemplateResponse("edit_server_page.html", {
             "request": request, "action_url": action_url, "submit_button_text": submit_button_text,
             "server_data": form_data_on_error, "form_title": f"Editing '{name}' (Server Error)",
             "is_add_form": False, "error": "Unexpected server error.",
             "cancel_url": request.url_for("ui_root"),
             "mcpo_settings": mcpo_settings # Pass settings
             }, status_code=500)

@router.get("/servers/add", response_class=HTMLResponse, name="ui_add_servers_form")
async def get_add_servers_page(
    request: Request,
    single_server_error: Optional[str] = None,
    single_server_form_data: Optional[dict] = None
):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
    
    mcpo_settings = config_service.load_mcpo_settings() # Load settings
    single_add_action_url = request.url_for("ui_add_single_server")
    bulk_analyze_action_url = request.url_for("ui_analyze_bulk_servers")
    
    return templates.TemplateResponse("add_servers_page.html", {
        "request": request,
        "single_add_action_url": single_add_action_url,
        "bulk_analyze_action_url": bulk_analyze_action_url,
        "single_server_form_data": single_server_form_data or {},
        "single_server_error": single_server_error,
        "mcpo_settings": mcpo_settings # Pass settings
    })

@router.post("/servers/add_single", name="ui_add_single_server")
async def handle_add_single_server_form(
    request: Request, db: Session = Depends(get_session),
    name: str = Form(...), server_type: str = Form(...), is_enabled: bool = Form(False),
    command: Optional[str] = Form(None), url: Optional[str] = Form(None),
    arg_items: List[str] = Form([], alias="arg_item[]"),
    env_keys: List[str] = Form([], alias="env_key[]"),
    env_values: List[str] = Form([], alias="env_value[]")
):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
    
    mcpo_settings = config_service.load_mcpo_settings() # Load settings for potential error re-render
    logger.info(f"UI Request: POST /servers/add_single (Adding server '{name}')")
    processed_args: List[str] = [arg for arg in arg_items if arg.strip()]
    processed_env_vars: Dict[str, str] = {}
    if len(env_keys) == len(env_values):
        for key, value in zip(env_keys, env_values):
            if key_stripped := key.strip():
                processed_env_vars[key_stripped] = value.strip()
    else:
        logger.warning("Mismatch in env_keys and env_values when adding server")

    final_command = command if command and command.strip() else None
    final_args = processed_args
    final_env_vars = processed_env_vars
    final_url = url if url and url.strip() else None
    error_msg: Optional[str] = None
    final_command, final_args = _deadapt_windows_command(final_command, final_args)

    if server_type == 'stdio':
        final_url = None
        if not final_command: error_msg = "The 'Command' field is mandatory for type 'stdio'."
    elif server_type in ['sse', 'streamable_http']:
        final_command = None; final_args = []; final_env_vars = {}
        if not final_url: error_msg = f"The 'URL' field is mandatory for type '{server_type}'."
    else:
        error_msg = "Unknown server type."

    form_data_on_error = {
        "name": name, "server_type": server_type, "is_enabled": is_enabled,
        "command": final_command, "args": final_args, "env_vars": final_env_vars, "url": final_url
    }
    single_add_action_url = request.url_for("ui_add_single_server")
    bulk_analyze_action_url = request.url_for("ui_analyze_bulk_servers")

    if error_msg:
        logger.warning(f"Validation error adding server '{name}': {error_msg}")
        return templates.TemplateResponse("add_servers_page.html", {
            "request": request, "single_add_action_url": single_add_action_url,
            "bulk_analyze_action_url": bulk_analyze_action_url,
            "single_server_form_data": form_data_on_error,
            "single_server_error": error_msg,
            "mcpo_settings": mcpo_settings # Pass settings
            }, status_code=400)
    try:
        definition_in = ServerDefinitionCreate(
            name=name, server_type=server_type, is_enabled=is_enabled,
            command=final_command, args=final_args, env_vars=final_env_vars, url=final_url
        )
        created = config_service.create_server_definition(db=db, definition_in=definition_in)
        redirect_url = str(request.url_for("ui_root")) + f"?single_add_success={quote(created.name)}"
        return RedirectResponse(url=redirect_url, status_code=303)
    except (ValueError, ValidationError) as e:
        error_text = f"Failed to add server: {str(e)}"
        logger.warning(f"Error adding server '{name}': {error_text}")
        return templates.TemplateResponse("add_servers_page.html", {
            "request": request, "single_add_action_url": single_add_action_url,
            "bulk_analyze_action_url": bulk_analyze_action_url,
            "single_server_form_data": form_data_on_error,
            "single_server_error": error_text,
            "mcpo_settings": mcpo_settings # Pass settings
            }, status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error adding server '{name}': {e}", exc_info=True)
        return templates.TemplateResponse("add_servers_page.html", {
             "request": request, "single_add_action_url": single_add_action_url,
             "bulk_analyze_action_url": bulk_analyze_action_url,
             "single_server_form_data": form_data_on_error,
             "single_server_error": "Unexpected server error during addition.",
             "mcpo_settings": mcpo_settings # Pass settings
             }, status_code=500)

@router.post("/servers/analyze-bulk", response_class=HTMLResponse, name="ui_analyze_bulk_servers")
async def handle_analyze_bulk_servers(
    request: Request, db: Session = Depends(get_session),
    config_json_str: str = Form(..., alias="configJsonStr"),
    default_enabled: bool = Form(False)
):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
    
    logger.info("UI Request: POST /servers/analyze-bulk (Analyzing JSON for bulk add)")
    analysis_result, parsing_errors = config_service.analyze_bulk_server_definitions(
        db=db, config_json_str=config_json_str, default_enabled=default_enabled
    )
    serialized_valid_servers = "[]"
    if analysis_result["valid_new"]:
        try:
            valid_new_dicts = [server.model_dump(mode='json') for server in analysis_result["valid_new"]]
            serialized_valid_servers = json.dumps(valid_new_dicts)
        except Exception as e:
            logger.error(f"Error serializing valid server data for confirmation: {e}", exc_info=True)
            parsing_errors.append("Internal error: Failed to prepare valid data for confirmation.")
            analysis_result["valid_new"] = []
            
    # mcpo_settings is not strictly needed by _bulk_add_preview.html itself,
    # but if it included other partials that extended base.html, it would be.
    # For safety or future-proofing, it could be passed, but currently not required by the partial alone.
    return templates.TemplateResponse("_bulk_add_preview.html", {
        "request": request, "analysis": analysis_result, "parsing_errors": parsing_errors,
        "serialized_valid_servers": serialized_valid_servers,
        "confirm_action_url": request.url_for("ui_confirm_bulk_add")
        # "mcpo_settings": config_service.load_mcpo_settings() # If _bulk_add_preview.html ever needs it for base
    })

@router.post("/servers/confirm-bulk-add", name="ui_confirm_bulk_add")
async def handle_confirm_bulk_add(
    request: Request, db: Session = Depends(get_session),
    valid_new_servers_json: str = Form(...)
):
    # This route redirects, so mcpo_settings isn't directly passed to a template here.
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for main UI router")
        
    logger.info(f"UI Request: POST /servers/confirm-bulk-add (Confirming bulk add)")
    added_count = 0
    errors: List[str] = []
    try:
        servers_to_add_data = json.loads(valid_new_servers_json)
        if not isinstance(servers_to_add_data, list):
            raise ValueError("Invalid payload format: Expected a list of server definitions.")
        if not servers_to_add_data:
             redirect_url = str(request.url_for("ui_root")) + "?bulk_info=No new servers were available to add."
             return RedirectResponse(url=redirect_url, status_code=303)
        logger.info(f"Attempting to add {len(servers_to_add_data)} servers from confirmed list.")
        for server_data in servers_to_add_data:
            server_name = server_data.get("name", "Unknown")
            try:
                definition_in = ServerDefinitionCreate(**server_data)
                config_service.create_server_definition(db=db, definition_in=definition_in)
                added_count += 1
            except (ValidationError, ValueError) as e:
                 msg = f"Error adding '{server_name}' during confirmation: {str(e)}"
                 errors.append(msg)
                 logger.warning(msg)
            except Exception as e:
                 msg = f"Unexpected error adding '{server_name}' during confirmation: {str(e)}"
                 errors.append(msg)
                 logger.error(msg, exc_info=True)
    except json.JSONDecodeError as e:
        errors.append(f"Failed to parse server data for confirmation: {e}")
        logger.error(f"JSON decode error during bulk confirmation: {e}")
    except Exception as e:
        errors.append(f"Unexpected error during confirmation process: {e}")
        logger.error(f"Unexpected error during bulk confirmation: {e}", exc_info=True)

    redirect_url_str = str(request.url_for("ui_root"))
    query_params = {}
    if added_count > 0:
        query_params["bulk_success"] = str(added_count)
        if errors:
            error_summary = f"Added {added_count} servers, but encountered {len(errors)} errors (see logs)."
            query_params["bulk_error"] = error_summary
    elif errors:
         error_summary = f"Failed to add servers during confirmation (Errors: {len(errors)} - see logs)."
         query_params["bulk_error"] = error_summary
    
    if query_params:
        encoded_params = "&".join(f"{k}={quote(str(v))}" for k, v in query_params.items())
        redirect_url_str += f"?{encoded_params}"
    return RedirectResponse(url=redirect_url_str, status_code=303)