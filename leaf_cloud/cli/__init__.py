"""
This package contains modules for handling the command-line interface commands.

This __init__.py file imports all command modules and exposes them in a
centralized list for easy registration in the main application.

Command Module Guidelines:
1. Each command module should define a function named `register_commands` (plural)
   that takes a Click group as its argument and registers the command with it.
2. The function signature should be: `def register_commands(cli: click.Group) -> None:`
3. This naming convention is required for the command to be properly registered
   in the main CLI application.
"""

from . import (
    analyze,
    clean,
    config,
    diagram,
    export,
    intermediate,
    simulate,
    version,
)

# A list of all available command modules for easy registration.
COMMAND_MODULES = [
    analyze,
    clean,
    config,
    diagram,
    export,
    intermediate,
    simulate,
    version,
]
try:
    # Best-effort: ensure 'optimize' module is importable and registered.
    from . import optimize as _optimize_mod  # noqa: F401
    try:
        COMMAND_MODULES.append(_optimize_mod)  # type: ignore
    except Exception:
        pass
except Exception:
    # Do not fail CLI import if optimize has missing deps
    pass
