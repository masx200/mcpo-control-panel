# ================================================
# FILE: src/mcp_manager_ui/models/server_definition.py
# ================================================
import json
from typing import Optional, List, Dict, Any
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON  # Using SQLAlchemy JSON type

# Database table model
class ServerDefinition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, description="Unique name for UI identification and as key in mcpServers config")
    is_enabled: bool = Field(default=False, index=True, description="Include this definition in generated mcpo_config.json?")
    server_type: str = Field(description="MCP server type ('stdio', 'sse', 'streamable_http')")

    # Fields for stdio
    command: Optional[str] = Field(default=None, description="Command to execute")
    # args and env_vars will be stored as JSON in the database
    args: Optional[List[str]] = Field(default_factory=list, sa_column=Column(JSON), description="Command arguments")
    env_vars: Optional[Dict[str, str]] = Field(default_factory=dict, sa_column=Column(JSON), description="Environment variables")

    # Fields for sse / streamable_http
    url: Optional[str] = Field(default=None, description="MCP server endpoint URL")


# --- Pydantic models for API and form validation ---

class ServerDefinitionBase(SQLModel):
    """Base model with fields common for creation and reading."""
    name: str
    is_enabled: bool = False
    server_type: str  # 'stdio', 'sse', 'streamable_http'
    command: Optional[str] = None
    # Using List[str] and Dict[str, str] directly for Pydantic models
    args: List[str] = Field(default_factory=list)
    env_vars: Dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None

    # Can add model_validator to check fields depending on server_type,
    # if this is not done in the route handler.
    # Example:
    # from pydantic import model_validator
    # @model_validator(mode='after')
    # def check_type_specific_fields(self) -> 'ServerDefinitionBase':
    #     if self.server_type == 'stdio':
    #         if not self.command:
    #             raise ValueError("Field 'command' is required for type 'stdio'")
    #         self.url = None  # Clear unnecessary field
    #     elif self.server_type in ['sse', 'streamable_http']:
    #         if not self.url:
    #             raise ValueError(f"Field 'url' is required for type '{self.server_type}'")
    #         self.command = None  # Clear unnecessary fields
    #         self.args = []
    #         self.env_vars = {}
    #     else:
    #         raise ValueError(f"Unknown server type: {self.server_type}")
    #     return self

class ServerDefinitionCreate(ServerDefinitionBase):
    """Model for data when creating a new definition (API/form)."""
    pass

class ServerDefinitionRead(ServerDefinitionBase):
    """Model for returning definition data from API (includes ID)."""
    id: int

class ServerDefinitionUpdate(SQLModel):
    """
    Model for partial definition update (API/form).
    All fields are optional.
    """
    name: Optional[str] = None
    is_enabled: Optional[bool] = None
    server_type: Optional[str] = None
    command: Optional[str] = None
    # When updating, we also expect complete lists/dictionaries,
    # not partial changes within them.
    args: Optional[List[str]] = None
    env_vars: Optional[Dict[str, str]] = None
    url: Optional[str] = None