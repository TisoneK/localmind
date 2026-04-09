"""
Shared Pydantic models used across LocalMind layers.
No business logic here — pure data shapes.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import time


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Intent(str, Enum):
    FILE_TASK = "file_task"
    WEB_SEARCH = "web_search"
    CODE_EXEC = "code_exec"
    SHELL = "shell"
    SYSINFO = "sysinfo"
    MEMORY_OP = "memory_op"
    FILE_WRITE = "file_write"
    CHAT = "chat"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Message(BaseModel):
    role: Role
    content: str
    timestamp: float = Field(default_factory=time.time)
    tool_name: Optional[str] = None
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None


class FileAttachment(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    # Raw bytes not stored in model — passed separately
    chunks: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    session_id: str
    # File attachment is handled as multipart, not in this model


class ChatResponse(BaseModel):
    text: str
    session_id: str
    intent: Intent
    tool_used: Optional[str] = None
    sources: list[str] = Field(default_factory=list)


class StreamChunk(BaseModel):
    text: str
    done: bool = False
    error: Optional[str] = None


class ToolResult(BaseModel):
    content: str
    risk: RiskLevel = RiskLevel.LOW
    source: str
    metadata: dict = Field(default_factory=dict)
    requires_confirmation: bool = False


class EngineContext(BaseModel):
    """Everything the context builder needs to assemble a prompt."""
    session_id: str
    message: str
    intent: Intent
    history: list[Message] = Field(default_factory=list)
    tool_result: Optional[ToolResult] = None
    file_attachment: Optional[FileAttachment] = None
    memory_facts: list[str] = Field(default_factory=list)
