# mcpo_control_panel/ui/routers/settings_routes.py
import logging
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.templating import Jinja2Templates

from ...services import config_service
from ...models.mcpo_settings import McpoSettings
from pydantic import ValidationError

logger = logging.getLogger(__name__)
router = APIRouter()
templates: Optional[Jinja2Templates] = None 

def set_templates_for_settings_routes(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates

@router.get("/settings", response_class="HTMLResponse", name="ui_edit_mcpo_settings_form")
async def get_mcpo_settings_form(request: Request):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for settings routes")
    settings = config_service.load_mcpo_settings()
    return templates.TemplateResponse("mcpo_settings_form.html", {
        "request": request,
        "settings": settings.model_dump(), # Pass current settings for display
        "error": None,
        "success": None
    })

@router.post("/settings", name="ui_update_mcpo_settings")
async def handle_update_mcpo_settings_form(
    request: Request,
    port: int = Form(...),
    public_base_url: Optional[str] = Form(None),
    api_key: Optional[str] = Form(None),
    use_api_key: bool = Form(False),
    config_file_path: str = Form(...),
    log_file_path: Optional[str] = Form(None),
    log_auto_refresh_enabled: bool = Form(False),
    log_auto_refresh_interval_seconds: int = Form(...),
    health_check_enabled: bool = Form(False),
    health_check_interval_seconds: Optional[int] = Form(None),
    health_check_failure_attempts: Optional[int] = Form(None),
    health_check_failure_retry_delay_seconds: Optional[int] = Form(None),
    auto_restart_on_failure: bool = Form(False)
    # manual_config_mode_enabled is NOT taken from this form anymore
):
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for settings routes")
    logger.info("UI Request: POST /settings (Updating subset of MCPO settings)")

    error_msg: Optional[str] = None
    success_msg: Optional[str] = None

    # Load current settings to preserve manual_config_mode_enabled and provide defaults
    current_settings = config_service.load_mcpo_settings()

    form_data_to_display = { # This will be updated with validated data if successful
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
        "auto_restart_on_failure": auto_restart_on_failure,
        "manual_config_mode_enabled": current_settings.manual_config_mode_enabled # Preserve this
    }

    try:
        clean_api_key = api_key if api_key and api_key.strip() else None
        clean_log_file_path = log_file_path if log_file_path and log_file_path.strip() else None
        clean_public_base_url = public_base_url if public_base_url and public_base_url.strip() else None
        
        model_defaults = McpoSettings.model_fields

        hc_interval = health_check_interval_seconds
        if health_check_interval_seconds is None or not health_check_enabled:
            hc_interval = model_defaults['health_check_interval_seconds'].default
        
        hc_attempts = health_check_failure_attempts
        if health_check_failure_attempts is None or not health_check_enabled:
            hc_attempts = model_defaults['health_check_failure_attempts'].default

        hc_retry_delay = health_check_failure_retry_delay_seconds
        if health_check_failure_retry_delay_seconds is None or not health_check_enabled:
            hc_retry_delay = model_defaults['health_check_failure_retry_delay_seconds'].default
            
        current_auto_restart = auto_restart_on_failure
        if not health_check_enabled:
            current_auto_restart = model_defaults['auto_restart_on_failure'].default

        settings_for_validation = McpoSettings(
            port=port,
            public_base_url=clean_public_base_url,
            api_key=clean_api_key,
            use_api_key=use_api_key,
            config_file_path=config_file_path,
            log_file_path=clean_log_file_path,
            log_auto_refresh_enabled=log_auto_refresh_enabled,
            log_auto_refresh_interval_seconds=log_auto_refresh_interval_seconds,
            health_check_enabled=health_check_enabled,
            health_check_interval_seconds=hc_interval,
            health_check_failure_attempts=hc_attempts,
            health_check_failure_retry_delay_seconds=hc_retry_delay,
            auto_restart_on_failure=current_auto_restart,
            # Preserve the manual_config_mode_enabled from currently loaded settings
            manual_config_mode_enabled=current_settings.manual_config_mode_enabled
        )

        if config_service.save_mcpo_settings(settings_for_validation):
            success_msg = "MCPO settings successfully updated."
            logger.info(success_msg)
            form_data_to_display = settings_for_validation.model_dump() # Display the newly saved data
        else:
            error_msg = "Failed to save MCPO settings."
            logger.error(error_msg)

    except ValidationError as ve:
        logger.warning(f"Validation error when updating MCPO settings: {ve.errors(include_url=False)}")
        error_details = [
            f"Field '{'.'.join(map(str, e['loc'])) if e['loc'] else 'field'}': {e['msg']}"
            for e in ve.errors(include_url=False)
        ]
        error_msg = "Validation errors: " + "; ".join(error_details)
    except Exception as e:
        logger.error(f"Unexpected error updating settings: {e}", exc_info=True)
        error_msg = f"An unexpected error occurred: {str(e)}"

    return templates.TemplateResponse(
        "mcpo_settings_form.html",
        {"request": request, "settings": form_data_to_display, "error": error_msg, "success": success_msg}
    )