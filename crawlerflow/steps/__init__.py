"""Built-in workflow steps."""

from __future__ import annotations

_loaded = False


def load_builtin_steps() -> None:
    """Import built-in steps once so their decorators populate the registry."""

    global _loaded
    if _loaded:
        return
    from crawlerflow.steps import browser, control, utility  # noqa: F401

    _loaded = True


__all__ = ["load_builtin_steps"]

