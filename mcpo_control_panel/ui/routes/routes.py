# mcpo_control_panel/ui/routes/routes.py (Main UI Router Aggregator)
import logging
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates
from typing import Optional

# Import the sub-router *modules*. Their router instances will be accessed via these module objects.
from . import main_ui_routes as main_ui_module
from . import settings_routes as settings_ui_module

logger = logging.getLogger(__name__)

router = APIRouter() # This is the router instance that this module provides.

# This function is called by mcpo_control_panel.ui.routes.__init__
# which in turn is called by main.py
def set_templates_for_ui_routers(jinja_templates: Jinja2Templates):
    """
    Sets the Jinja2Templates instance for all UI sub-routers.
    This function is effectively called from main.py via the package's __init__.
    """
    logger.debug(
        f"Attempting to set templates for UI sub-routers. "
        f"main_ui_module has set_templates: {hasattr(main_ui_module, 'set_templates_for_main_ui_routes')}, "
        f"settings_ui_module has set_templates: {hasattr(settings_ui_module, 'set_templates_for_settings_routes')}"
    )
    
    if hasattr(main_ui_module, 'set_templates_for_main_ui_routes'):
        main_ui_module.set_templates_for_main_ui_routes(jinja_templates)
    else:
        logger.error("main_ui_module does not have 'set_templates_for_main_ui_routes' function.")

    if hasattr(settings_ui_module, 'set_templates_for_settings_routes'):
        settings_ui_module.set_templates_for_settings_routes(jinja_templates)
    else:
        logger.error("settings_ui_module does not have 'set_templates_for_settings_routes' function.")

    logger.info("Jinja2 templates distribution to UI sub-routers attempted.")

# Include the .router attributes from the imported sub-modules
router.include_router(main_ui_module.router)
router.include_router(settings_ui_module.router)

logger.info("UI sub-routers (main_ui_routes, settings_routes) included into this UI aggregator router.")