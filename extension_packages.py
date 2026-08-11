"""Directory-based WenMo extension packages (DLCs).

Each package lives in one directory containing ``wenmo-extension.json`` and
optional ``plugins/``, ``skills/`` and ``mcp.json`` components. Development
packages are never searched by packaged clients.
"""
import json
import os
import re
import sys

MANIFEST = "wenmo-extension.json"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def package_roots():
    data = os.environ.get("WENMO_DATA_DIR", "")
    roots = []
    if data:
        roots.append(os.path.join(data, "content", "extensions"))
    if not getattr(sys, "frozen", False):
        roots.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "extensions"))
    return roots


def discover_packages():
    packages = []
    seen = set()
    for root in package_roots():
        if not os.path.isdir(root):
            continue
        for folder in sorted(os.listdir(root)):
            path = os.path.realpath(os.path.join(root, folder))
            if not os.path.isdir(path) or not path.startswith(os.path.realpath(root) + os.sep):
                continue
            manifest_path = os.path.join(path, MANIFEST)
            try:
                with open(manifest_path, encoding="utf-8") as handle:
                    manifest = json.load(handle)
                name = str(manifest.get("name") or folder).strip().lower()
                if not _NAME_RE.fullmatch(name) or name in seen or manifest.get("enabled") is False:
                    continue
                seen.add(name)
                packages.append({"name": name, "path": path, "manifest": manifest})
            except (OSError, ValueError, TypeError):
                continue
    return packages


def component_dirs(component):
    result = []
    for package in discover_packages():
        path = os.path.join(package["path"], component)
        if os.path.isdir(path):
            result.append(path)
    return result


def mcp_servers():
    servers = {}
    for package in discover_packages():
        path = os.path.join(package["path"], "mcp.json")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            for name, entry in (data.get("servers") or {}).items():
                if isinstance(entry, dict) and entry.get("enabled") is not False:
                    servers.setdefault(str(name), entry)
        except (OSError, ValueError, TypeError):
            continue
    return servers
