# ================================================
# FILE: src/mcp_manager_ui/api/mcpo_control.py
# ================================================
import logging
import html
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlmodel import Session
from fastapi.templating import Jinja2Templates

import os

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
    if not config_service.generate_mcpo_config_file(db, settings):
        error_message = "Failed to generate standard MCPO configuration file."
        logger.error(error_message)
        return templates.TemplateResponse(
            "_mcpo_status.html",
            {"request": request, "mcpo_status": mcpo_service.get_mcpo_status(), "message": error_message},
            status_code=500
        )
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
    """
    Returns HTML fragment with the latest log lines (escaped HTML).
    Designed for use with HTMX (inserted into <code>).
    """
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

# --- Get Generated Config ---
@router.get("/generated-config", response_class=PlainTextResponse)
async def get_generated_mcpo_config_content(
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """
    Returns the content of the **standard** generated MCPO configuration file.
    """
    logger.debug("API call: Get standard generated MCPO config content")
    config_path = settings.config_file_path
    error_prefix = "Error getting standard config: "

    if not config_path:
        logger.warning(f"{error_prefix}Configuration file path not set in settings.")
        return PlainTextResponse(content=f"{error_prefix}Configuration file path not set.", status_code=404)

    if not os.path.exists(config_path):
        logger.warning(f"{error_prefix}File '{config_path}' not found.")
        return PlainTextResponse(content=f"{error_prefix}File '{config_path}' not found.", status_code=404)

    try:
        with open(config_path, 'r', encoding='utf-8') as f: content = f.read()
        return PlainTextResponse(content=content, media_type="application/json")
    except Exception as e:
        logger.error(f"Error reading standard configuration file '{config_path}': {e}", exc_info=True)
        return PlainTextResponse(content=f"{error_prefix}Error reading file '{config_path}'.", status_code=500)

# --- Windows Config Endpoint ---
@router.get("/generated-config-windows", response_class=PlainTextResponse)
async def get_generated_mcpo_config_content_windows(
    db: Session = Depends(get_session),
    settings: McpoSettings = Depends(get_mcpo_settings_dependency)
):
    """
    Generates and returns MCPO configuration content adapted for Windows.
    """
    logger.debug("API call: Get Windows-adapted generated MCPO config content")
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


# --- MCPO Settings Management ---
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

# Function to pass templates from main.py
def set_templates_for_api(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates