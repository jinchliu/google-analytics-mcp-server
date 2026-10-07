"""Tool metadata and runtime registration independent of Google credentials."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp.types import ToolAnnotations

from google_analytics_mcp_server import config, policy

F = TypeVar('F', bound=Callable[..., Any])


@dataclass(frozen=True)
class ToolEntry:
    """A tool definition, retained even when its runtime feature is disabled."""

    fn: Callable[..., Any]
    tier: str
    feature: str | None


REGISTRY: dict[str, ToolEntry] = {}


def tool(*, tier: str | None = None, feature: str | None = None) -> Callable[[F], F]:
    """Record a function without binding it to a process-global server."""

    def decorator(fn: F) -> F:
        selected = tier or (
            'read'
            if fn.__name__ in policy.READ_OWNERS
            else 'destructive'
            if fn.__name__ in policy.DESTRUCTIVE_OWNERS
            else 'job'
            if fn.__name__ in policy.JOB_OWNERS
            else 'write'
        )
        REGISTRY[fn.__name__] = ToolEntry(fn, selected, feature)
        return fn

    return decorator


def available(entry: ToolEntry) -> bool:
    """Apply strict read-only and optional-feature registration filters."""
    if config.read_only() and entry.tier != 'read':
        return False
    return entry.feature is None or config.enabled(entry.feature)


def annotations(entry: ToolEntry) -> ToolAnnotations:
    """Annotate actual effects; these hints never replace policy checks."""
    read = entry.tier == 'read'
    return ToolAnnotations(
        read_only_hint=read,
        destructive_hint=not read and entry.tier != 'job',
        idempotent_hint=read,
        open_world_hint=entry.fn.__name__ != 'ga_describe_schema',
    )
