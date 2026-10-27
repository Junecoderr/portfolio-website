"""tbtools: the `tb <sub>` command-line tools.

Each tool is a module exposing `main(args: list[str]) -> int`. `tb.py`
dispatches `tb <sub> ...` to `importlib.import_module(f"tbtools.{sub}").main(args)`.

Several sub-commands share one implementation (the package manager lives in
`pkg.py` and answers to `tb add`, `tb remove`, ...). Rather than one shim file
per name, the table below maps each sub-command to (module, first argument)
and `tbtools.<alias>` is registered as a tiny module in `sys.modules`, so the
dispatch rule in tb.py needs no special cases.

Wave 1 (TB 3.0): pkg. Wave 2 adds fmt, check, lsp, debug, docgen, build.
"""

from __future__ import annotations

import importlib
import sys
import types

__all__ = ["COMMANDS", "dispatch", "available_commands"]

# sub-command -> (module under tbtools, argument list prepended to the user's)
COMMANDS: dict[str, tuple[str, list[str]]] = {
    "init": ("pkg", ["init"]),
    "add": ("pkg", ["add"]),
    "remove": ("pkg", ["remove"]),
    "update": ("pkg", ["update"]),
    "packages": ("pkg", ["packages"]),
    "publish": ("pkg", ["publish"]),
    "login": ("pkg", ["login"]),
    "logout": ("pkg", ["logout"]),
    "search": ("pkg", ["search"]),
    "info": ("pkg", ["info"]),
    "yank": ("pkg", ["yank"]),
    "pkg": ("pkg", []),
}


def available_commands() -> list[str]:
    """The sub-command names `tb` can dispatch (sorted)."""
    return sorted(COMMANDS)


def dispatch(sub: str, args: list[str]) -> int:
    """Run `tb <sub> args...`. Raises KeyError for an unknown sub-command."""
    module_name, prefix = COMMANDS[sub]
    module = importlib.import_module(f"tbtools.{module_name}")
    return module.main([*prefix, *list(args)])


def _register_alias(sub: str, module_name: str, prefix: list[str]) -> None:
    if sub == module_name:
        return  # a real module file; importlib finds it on its own
    full = f"{__name__}.{sub}"
    if full in sys.modules:
        return
    shim = types.ModuleType(full, f"`tb {sub}` -> tbtools.{module_name}.main({prefix + ['...']})")

    def main(args: list[str] | None = None, _m=module_name, _p=tuple(prefix)) -> int:
        module = importlib.import_module(f"tbtools.{_m}")
        return module.main([*_p, *list(args or [])])

    shim.main = main  # type: ignore[attr-defined]
    shim.__package__ = __name__
    sys.modules[full] = shim
    setattr(sys.modules[__name__], sub, shim)


for _sub, (_mod, _prefix) in COMMANDS.items():
    _register_alias(_sub, _mod, _prefix)
