"""Plugin registry for hydra_crud.

Maps platform name -> Plugin class. Auto-discovers plugins in this package's
plugins/ subdirectory by importing every module that declares a Plugin
subclass with a non-empty `platform`.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Type

from .plugin import Plugin


class RegistryError(Exception):
    pass


_REGISTRY: dict[str, Type[Plugin]] = {}


def _discover() -> None:
    """Import every module under hydra_crud.plugins and register plugins."""
    import hydra_crud.plugins as plugins_pkg

    for modinfo in pkgutil.iter_modules(plugins_pkg.__path__):
        try:
            importlib.import_module(f"hydra_crud.plugins.{modinfo.name}")
        except ImportError:
            continue


def register(plugin_cls: Type[Plugin]) -> None:
    if not plugin_cls.platform:
        raise RegistryError(
            f"Plugin {plugin_cls.__name__} must declare a non-empty platform")
    if plugin_cls.platform in _REGISTRY:
        raise RegistryError(f"Duplicate platform: {plugin_cls.platform}")
    _REGISTRY[plugin_cls.platform] = plugin_cls


def get(platform: str) -> Plugin:
    if not _REGISTRY:
        _discover()
    cls = _REGISTRY.get(platform)
    if cls is None:
        raise RegistryError(
            f"Unknown platform '{platform}'. Known: {', '.join(sorted(_REGISTRY))}")
    return cls()


def known_platforms() -> list[str]:
    if not _REGISTRY:
        _discover()
    return sorted(_REGISTRY)
