# mcpo_control_panel/services/config_managers/definition_manager.py
import logging
from typing import List, Optional
from sqlmodel import Session, select

from ...models.server_definition import (
    ServerDefinition, ServerDefinitionCreate, ServerDefinitionUpdate
)

logger = logging.getLogger(__name__)

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