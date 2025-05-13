# mcpo_control_panel/ui/routes/__init__.py
import logging

# Import the actual aggregator module (routes.py in this directory)
# and expose its 'router' instance and 'set_templates_for_ui_routers' function.
from .routes import router as main_ui_aggregator_router
from .routes import set_templates_for_ui_routers as main_set_templates_function

# Make them available at the package level (mcpo_control_panel.ui.routes)
router = main_ui_aggregator_router
set_templates_for_ui_routers = main_set_templates_function

_init_logger = logging.getLogger(__name__)
_init_logger.info(
    "Package mcpo_control_panel.ui.routes initialized, "
    "exposing 'router' and 'set_templates_for_ui_routers' from its 'routes.py' module."
)

__all__ = ['router', 'set_templates_for_ui_routers']