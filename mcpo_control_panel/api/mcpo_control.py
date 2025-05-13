# ================================================
# FILE: mcpo_control_panel/api/mcpo_control.py
# (Handle empty string for manual config content)
# ================================================
import logging
import html
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Body
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlmodel import Session
from fastapi.templating import Jinja2Templates
from fastapi import Form

import os 
import json # Moved import to top

from ..db.database import get_session
from ..services import mcpo_service, config_service
from ..models.mcpo_settings import McpoSettings

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None

def get_mcpo_settings_dependency() -> McpoSettings:
     return config_service.load_mcpo_settings()

# --- MCPO Process Management ---
@router.post("/start", response_class=HTMLResponse)
async def start_mcpo_process(
    request: Request,
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    logger.info("API call: Start MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")

    if not settings.manual_config_mode_enabled:
        if not config_service.generate_mcpo_config_file(db, settings):
            error_message = "Failed to generate standard MCPO configuration file."
            logger.error(error_message)
            return templates.TemplateResponse(
                "_mcpo_status.html",
                {"request": request, "mcpo_status": mcpo_service.get_mcpo_status(), "message": error_message},
                status_code=500
            )
    else:
        config_service.generate_mcpo_config_file(db, settings)


    success, message = await mcpo_service.start_mcpo(settings)
    current_status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )


@router.post("/stop", response_class=HTMLResponse)
async def stop_mcpo_process(request: Request):
    logger.info("API call: Stop MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")
    success, message = await mcpo_service.stop_mcpo()
    current_status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )


@router.post("/restart", response_class=HTMLResponse)
async def restart_mcpo_process(
    request: Request,
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    logger.info("API call: Restart MCPO process")
    if not templates: raise HTTPException(500, "Templates not configured")
    success, message = await mcpo_service.restart_mcpo_process_with_new_config(db, settings)
    current_status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": current_status, "message": message}
    )

@router.get("/status", response_class=HTMLResponse)
async def get_mcpo_process_status_html(request: Request):
    logger.debug("API call: Get MCPO status HTML")
    if not templates: raise HTTPException(500, "Templates not configured")
    status = mcpo_service.get_mcpo_status()
    return templates.TemplateResponse(
        "_mcpo_status.html",
        {"request": request, "mcpo_status": status}
    )

@router.get("/logs", response_class=HTMLResponse, name="api_get_logs_html_content")
async def get_mcpo_process_logs_html(
    request: Request,
    lines: int = 100,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    logger.debug(f"API call: Get MCPO logs HTML (last {lines} lines)")
    if not templates: raise HTTPException(500, "Templates not configured")
    if not settings.log_file_path:
        return HTMLResponse("<pre><code>Log file path not configured.</code></pre>")
    if not os.path.exists(settings.log_file_path):
        return HTMLResponse(f"<pre><code>Log file not found: {html.escape(settings.log_file_path)}</code></pre>")

    log_lines = await mcpo_service.get_mcpo_logs(lines, settings.log_file_path)
    log_content = "\n".join(log_lines)
    escaped_logs = html.escape(log_content)
    return HTMLResponse(f"<pre><code>{escaped_logs}</code></pre>")

@router.get("/logs/content", response_class=HTMLResponse, name="api_get_logs_content_html")
async def get_mcpo_process_logs_html_fragment(
    lines: int = 200,
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    logger.debug(f"API call (HTMX): Get MCPO logs HTML fragment (last {lines} lines)")

    if not settings.log_file_path:
        logger.warning("API call (HTMX): Log file path not configured.")
        return HTMLResponse("Log file path not configured.")

    if not os.path.exists(settings.log_file_path):
        logger.warning(f"API call (HTMX): Log file not found at '{settings.log_file_path}'.")
        return HTMLResponse(f"Log file not found: {html.escape(settings.log_file_path)}")

    try:
        log_lines = await mcpo_service.get_mcpo_logs(lines, settings.log_file_path)
        if log_lines and log_lines[0].startswith("Error:"):
             log_content = "\n".join(log_lines)
             escaped_logs = html.escape(log_content)
        elif log_lines:
             log_content = "\n".join(log_lines)
             escaped_logs = html.escape(log_content).replace('\n', '<br>')
        else:
             escaped_logs = "Log file is empty."

        return HTMLResponse(content=escaped_logs)
    except Exception as e:
        logger.error(f"API call (HTMX): Error reading log file '{settings.log_file_path}': {e}", exc_info=True)
        return HTMLResponse(f"Error reading log file: {html.escape(str(e))}")

@router.get("/generated-config", response_class=PlainTextResponse, name="get_generated_mcpo_config_content")
async def get_generated_mcpo_config_content_api( 
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    if settings.manual_config_mode_enabled:
        logger.debug("API call: Get manual MCPO config content (from disk)")
        context_message = "manual config"
    else:
        logger.debug("API call: Get standard generated MCPO config content")
        context_message = "standard generated config"

    config_path = settings.config_file_path
    error_prefix = f"Error getting {context_message}: "

    if not config_path:
        logger.warning(f"{error_prefix}Configuration file path not set in settings.")
        return PlainTextResponse(content=f"{error_prefix}Configuration file path not set.", status_code=404)

    if not os.path.exists(config_path):
        logger.warning(f"{error_prefix}File '{config_path}' not found.")
        if settings.manual_config_mode_enabled:
            return PlainTextResponse(content="{}", media_type="application/json", status_code=200) 
        return PlainTextResponse(content=f"{error_prefix}File '{config_path}' not found.", status_code=404)

    try:
        with open(config_path, 'r', encoding='utf-8') as f: content = f.read()
        # If content is empty, return a default JSON object string for consistency
        if not content.strip() and settings.manual_config_mode_enabled:
            return PlainTextResponse(content="{}", media_type="application/json")
        return PlainTextResponse(content=content, media_type="application/json")
    except Exception as e:
        logger.error(f"Error reading {context_message} file '{config_path}': {e}", exc_info=True)
        return PlainTextResponse(content=f"{error_prefix}Error reading file '{config_path}'.", status_code=500)


@router.post("/manual-config-content", response_class=PlainTextResponse, name="set_manual_config_content_api")
async def set_manual_config_content_api(
    settings: McpoSettings = Depends(get_mcpo_settings_dependency),
    manual_config_content_area_main: str = Form(..., alias="manual_config_content_area_main") 
):
    logger.info("API call: Save manual MCPO config content to disk")
    if not settings.manual_config_mode_enabled:
        logger.error("Attempted to save manual config when manual mode is disabled.")
        raise HTTPException(status_code=403, detail="Manual configuration mode is not enabled.")

    config_path = settings.config_file_path
    if not config_path:
        logger.error("Failed to save manual config: Configuration file path not set in settings.")
        raise HTTPException(status_code=500, detail="Configuration file path not set.")

    # Use the correct variable name as defined in the parameters
    content_to_validate = manual_config_content_area_main 
    content_to_save = content_to_validate.strip() 
    
    if not content_to_save:
        logger.info("Manual config content is empty. Saving default empty JSON object: {}")
        content_to_save = json.dumps({"mcpServers": {}}, indent=2)
    else:
        try:
            parsed_json = json.loads(content_to_save)
            content_to_save = json.dumps(parsed_json, indent=2)
        except json.JSONDecodeError as json_e:
            logger.warning(f"Invalid JSON content provided for manual config: {json_e}. Content: '{content_to_validate[:200]}...'")
            raise HTTPException(status_code=400, detail=f"Invalid JSON format: {json_e}")

    try:
        config_dir = os.path.dirname(config_path)
        if config_dir: 
            os.makedirs(config_dir, exist_ok=True)

        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(content_to_save)
        logger.info(f"Manual MCPO configuration successfully saved to {config_path}")
        return PlainTextResponse(content="Manual configuration saved successfully.", status_code=200)
    except IOError as e:
        logger.error(f"IOError saving manual configuration file '{config_path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error writing file '{config_path}'.")
    except Exception as e:
        logger.error(f"Unexpected error saving manual configuration file '{config_path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Unexpected server error saving manual configuration.")


@router.get("/generated-config-windows", response_class=PlainTextResponse, name="get_generated_mcpo_config_content_windows")
async def get_generated_mcpo_config_content_windows_api( 
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    logger.debug("API call: Get Windows-adapted MCPO config content")

    if settings.manual_config_mode_enabled:
        logger.info("Manual config mode: Serving raw config for Windows download with a warning.")
        config_path = settings.config_file_path
        if not config_path or not os.path.exists(config_path):
            warning_content = "// WARNING: Manual configuration mode. Windows adaptations are NOT applied automatically.\n"
            warning_content += "// Config file not found or path not set.\n{}"
            return PlainTextResponse(content=warning_content, media_type="application/json", status_code=200) 
        try:
            with open(config_path, 'r', encoding='utf-8') as f: content = f.read()
            if not content.strip(): # If file is empty
                content = "{}" 
            warning_content = "// WARNING: Manual configuration mode. Windows adaptations are NOT applied automatically.\n\n"
            return PlainTextResponse(content=warning_content + content, media_type="application/json")
        except Exception as e:
            logger.error(f"Error reading manual config file '{config_path}' for Windows download: {e}", exc_info=True)
            return PlainTextResponse(content=f"// Error reading manual config file for Windows download.", status_code=500)

    try:
        windows_config_content = config_service.generate_mcpo_config_content_for_windows(db, settings)
        if windows_config_content.startswith("// Error generating Windows config:"):
            logger.error(f"Error generating Windows config: {windows_config_content}")
            return PlainTextResponse(content=windows_config_content, status_code=500)
        else:
             return PlainTextResponse(content=windows_config_content, media_type="application/json")
    except Exception as e:
        logger.error(f"Unexpected error getting Windows config: {e}", exc_info=True)
        return PlainTextResponse(content=f"// Unexpected server error generating Windows config.", status_code=500)

@router.get("/settings", response_model=McpoSettings)
async def get_settings(settings: McpoSettings = Depends(get_mcpo_settings_dependency)):
    logger.debug("API call: GET /settings")
    return settings

@router.post("/settings", response_model=McpoSettings)
async def update_settings(new_settings_payload: McpoSettings):
    logger.info("API call: POST /settings (Update all settings)")
    if config_service.save_mcpo_settings(new_settings_payload):
        return new_settings_payload
    else:
        raise HTTPException(status_code=500, detail="Failed to save MCPO settings.")

def set_templates_for_api(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates