"""hydra_crud -- CRUD plugin framework for hydra-publish.

Each platform is a Plugin subclass implementing create/read/update/delete
(and optionally list). The orchestrator (hydra-publish) routes CRUD verbs
through the registry to the platform plugin.
"""

from .model import Post, PlatformRef, PostStatus
from .plugin import Plugin, PluginError

__all__ = ["Post", "PlatformRef", "PostStatus", "Plugin", "PluginError"]
