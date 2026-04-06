"""
Tool base types. All tools return ToolResult.
Import from core.models — this file re-exports for tool author convenience.
"""
from core.models import ToolResult, RiskLevel as ToolRisk

__all__ = ["ToolResult", "ToolRisk"]
