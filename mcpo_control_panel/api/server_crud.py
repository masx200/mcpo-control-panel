# ================================================
# FILE: mcpo_control_panel/api/server_crud.py
# ================================================
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session
from fastapi.templating import Jinja2Templates

from ..db.database import get_session
from ..services import config_service
from ..models.server_definition import ServerDefinitionRead

# Logger setup
logger = logging.getLogger(__name__)

router = APIRouter()

# Templates variable (set from main.py)
templates: Optional[Jinja2Templates] = None

# --- API for HTMX interaction with server definitions ---

@router.post("/{server_id}/toggle", response_class=HTMLResponse)
async def toggle_server(
    request: Request,
    server_id: int,
    db: Session = Depends(get_session)
):
    """
    Toggles the is_enabled flag for a server and returns the updated table row (HTML).
    """
    logger.info(f"API Request: POST /api/servers/{server_id}/toggle")
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured for API router")

    updated_definition = config_service.toggle_server_enabled(db, server_id)
    if not updated_definition:
        raise HTTPException(status_code=404, detail="Server definition not found")

    definition_read = ServerDefinitionRead.model_validate(updated_definition)

    return templates.TemplateResponse(
        "_server_row.html",
        {"request": request, "server": definition_read}
    )

@router.delete("/{server_id}", status_code=200)
async def delete_server(
    server_id: int,
    db: Session = Depends(get_session)
):
    """
    Deletes a server definition. Returns an empty response.
    """
    logger.info(f"API Request: DELETE /api/servers/{server_id}")
    deleted = config_service.delete_server_definition(db, server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Server definition not found")

    # Important: Return an empty response so HTMX removes the element from the page
    return Response(status_code=200)

# Function to pass templates from main.py
def set_templates_for_api(jinja_templates: Jinja2Templates):
    global templates
    templates = jinja_templates