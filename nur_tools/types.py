"""Thin re-export layer for tool-related types.

Source of truth is core/types.py. Import from here for convenience
within the tools/ package.
"""

from core.types import (  # noqa: F401
    ActionVariables,
    ToolCapability,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
)
