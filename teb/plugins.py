"""
Execution Plugin System (Step 1).

A lightweight plugin API for execution capabilities — plugins can register:
- What task types they handle
- What credentials they need
- An execute() function
- Lifecycle hooks: before_execute, after_execute, on_error

Plugins are discovered from the `plugins/` directory or registered via API.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from teb import storage
from teb.models import PluginManifest

logger = logging.getLogger(__name__)


# ─── Plugin Interface ────────────────────────────────────────────────────────

@dataclass
class PluginCapability:
    """What a plugin can do."""
    task_types: List[str]              # e.g. ["email_send", "dns_setup"]
    required_credentials: List[str]    # e.g. ["sendgrid_api_key"]
    description: str = ""


@dataclass
class PluginResult:
    """Result from a plugin execution."""
    success: bool
    output: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


@dataclass
class PluginLifecycle:
    """Lifecycle hooks for a plugin.

    All hooks are optional.  A plugin module may define any of:
    - ``before_execute(task_context, credentials) -> Optional[dict]``
      Called before ``execute()``.  May mutate *task_context*.
      Return ``None`` to continue or a *PluginResult*-like dict to short-circuit.
    - ``after_execute(task_context, credentials, result) -> Optional[PluginResult]``
      Called after ``execute()`` succeeds.  May enrich/transform the result.
    - ``on_error(task_context, credentials, error) -> Optional[PluginResult]``
      Called when ``execute()`` raises.  May return a fallback result.
    """
    before_execute: Optional[Callable] = None
    after_execute: Optional[Callable] = None
    on_error: Optional[Callable] = None


# ─── Plugin Registry ─────────────────────────────────────────────────────────

# In-memory registry of loaded plugin executors
_PLUGIN_EXECUTORS: Dict[str, Callable] = {}

# In-memory registry of plugin lifecycle hooks
_PLUGIN_LIFECYCLE: Dict[str, PluginLifecycle] = {}


def register_executor(plugin_name: str, executor_fn: Callable) -> None:
    """Register an in-memory executor function for a plugin."""
    _PLUGIN_EXECUTORS[plugin_name] = executor_fn


def unregister_executor(plugin_name: str) -> None:
    """Remove an in-memory executor."""
    _PLUGIN_EXECUTORS.pop(plugin_name, None)


def get_executor(plugin_name: str) -> Optional[Callable]:
    """Get the executor function for a plugin."""
    return _PLUGIN_EXECUTORS.get(plugin_name)


def list_loaded_plugins() -> List[str]:
    """List names of all loaded (in-memory) plugins."""
    return list(_PLUGIN_EXECUTORS.keys())


# ─── Plugin Discovery ────────────────────────────────────────────────────────

def discover_plugins(plugins_dir: Optional[str] = None) -> List[PluginManifest]:
    """Discover plugins from directory. Each plugin is a subdirectory containing
    a manifest.json and a plugin.py with an execute() function.

    manifest.json schema:
    {
        "name": "sendgrid-email",
        "version": "1.0.0",
        "description": "Send emails via SendGrid API",
        "task_types": ["email_send", "email_campaign"],
        "required_credentials": ["sendgrid"]
    }
    """
    if plugins_dir is None:
        plugins_dir = os.path.join(os.path.dirname(__file__), "..", "plugins")

    plugins_path = Path(plugins_dir)
    if not plugins_path.exists():
        return []

    discovered: List[PluginManifest] = []
    for entry in plugins_path.iterdir():
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            plugin = PluginManifest(
                name=manifest.get("name", entry.name),
                version=manifest.get("version", "0.1.0"),
                description=manifest.get("description", ""),
                task_types=json.dumps(manifest.get("task_types", [])),
                required_credentials=json.dumps(manifest.get("required_credentials", [])),
                module_path=str(entry / "plugin.py"),
                enabled=True,
            )
            discovered.append(plugin)
        except Exception as e:
            logger.warning("Failed to load plugin from %s: %s", entry, e)

    return discovered


def load_plugin(plugin: PluginManifest) -> bool:
    """Load a plugin's executor and lifecycle hooks from its module_path.

    For safety, the module_path must be an existing file. It is validated
    to prevent loading modules from arbitrary locations.
    """
    if not plugin.module_path or not os.path.exists(plugin.module_path):
        logger.warning("Plugin %s has no valid module_path: %s", plugin.name, plugin.module_path)
        return False
    # Resolve to real path to prevent symlink tricks
    real_path = os.path.realpath(plugin.module_path)
    if not os.path.isfile(real_path):
        logger.warning("Plugin %s module_path is not a file: %s", plugin.name, real_path)
        return False
    try:
        spec = importlib.util.spec_from_file_location(f"teb_plugin_{plugin.name}", real_path)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "execute"):
            register_executor(plugin.name, module.execute)
            # Load lifecycle hooks if present
            lifecycle = PluginLifecycle(
                before_execute=getattr(module, "before_execute", None),
                after_execute=getattr(module, "after_execute", None),
                on_error=getattr(module, "on_error", None),
            )
            _PLUGIN_LIFECYCLE[plugin.name] = lifecycle
            return True
        logger.warning("Plugin %s has no execute() function", plugin.name)
        return False
    except Exception as e:
        logger.error("Failed to load plugin %s: %s", plugin.name, e)
        return False


def sync_plugins_from_directory(plugins_dir: Optional[str] = None) -> List[PluginManifest]:
    """Discover plugins from disk and register them in the database + memory."""
    discovered = discover_plugins(plugins_dir)
    synced: List[PluginManifest] = []
    for plugin in discovered:
        existing = storage.get_plugin(plugin.name)
        if not existing:
            plugin = storage.create_plugin(plugin)
        else:
            plugin.id = existing.id
        if load_plugin(plugin):
            synced.append(plugin)
    return synced


def execute_plugin(plugin_name: str, task_context: Dict[str, Any],
                   credentials: Dict[str, str]) -> PluginResult:
    """Execute a plugin for a given task context, with lifecycle hooks.

    Lifecycle order:
    1. ``before_execute(task_context, credentials)`` — may short-circuit
    2. ``execute(task_context, credentials)`` — main execution
    3. ``after_execute(task_context, credentials, result)`` — may transform result
    On error: ``on_error(task_context, credentials, error)`` — may provide fallback
    """
    executor_fn = get_executor(plugin_name)
    if not executor_fn:
        return PluginResult(success=False, error=f"Plugin '{plugin_name}' not loaded")

    plugin = storage.get_plugin(plugin_name)
    if plugin and not plugin.enabled:
        return PluginResult(success=False, error=f"Plugin '{plugin_name}' is disabled")

    lifecycle = _PLUGIN_LIFECYCLE.get(plugin_name, PluginLifecycle())

    try:
        # ── before_execute hook ──
        if lifecycle.before_execute:
            try:
                pre_result = lifecycle.before_execute(task_context, credentials)
                if pre_result is not None:
                    # Short-circuit: hook returned a result
                    if isinstance(pre_result, PluginResult):
                        return pre_result
                    if isinstance(pre_result, dict):
                        return PluginResult(
                            success=pre_result.get("success", True),
                            output=pre_result.get("output", ""),
                            error=pre_result.get("error", ""),
                            metadata=pre_result.get("metadata", {}),
                        )
            except Exception as hook_err:
                logger.warning("Plugin %s before_execute hook failed: %s", plugin_name, hook_err)

        # ── main execution ──
        result = executor_fn(task_context, credentials)
        if isinstance(result, PluginResult):
            plugin_result = result
        elif isinstance(result, dict):
            plugin_result = PluginResult(
                success=result.get("success", True),
                output=result.get("output", ""),
                error=result.get("error", ""),
                metadata=result.get("metadata", {}),
            )
        else:
            plugin_result = PluginResult(success=True, output=str(result))

        # ── after_execute hook ──
        if lifecycle.after_execute:
            try:
                post_result = lifecycle.after_execute(task_context, credentials, plugin_result)
                if isinstance(post_result, PluginResult):
                    plugin_result = post_result
            except Exception as hook_err:
                logger.warning("Plugin %s after_execute hook failed: %s", plugin_name, hook_err)

        return plugin_result

    except Exception as e:
        # ── on_error hook ──
        if lifecycle.on_error:
            try:
                error_result = lifecycle.on_error(task_context, credentials, e)
                if isinstance(error_result, PluginResult):
                    return error_result
                if isinstance(error_result, dict):
                    return PluginResult(
                        success=error_result.get("success", False),
                        output=error_result.get("output", ""),
                        error=error_result.get("error", str(e)),
                        metadata=error_result.get("metadata", {}),
                    )
            except Exception as hook_err:
                logger.warning("Plugin %s on_error hook failed: %s", plugin_name, hook_err)
        return PluginResult(success=False, error=str(e))


def find_plugins_for_task(task_type: str) -> List[PluginManifest]:
    """Find all enabled plugins that can handle a given task type."""
    all_plugins = storage.list_plugins(enabled_only=True)
    matching: List[PluginManifest] = []
    for p in all_plugins:
        try:
            types = json.loads(p.task_types)
        except (json.JSONDecodeError, TypeError):
            types = []
        if task_type in types:
            matching.append(p)
    return matching
