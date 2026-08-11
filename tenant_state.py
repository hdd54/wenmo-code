"""Small tenant-scoped state primitives shared by server-side subsystems."""

import json
import os
import re
import threading

from execution_context import current_tenant, current_workspace


def tenant_name():
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", str(current_tenant.get() or "local"))
    return value[:120] or "local"


def data_root():
    return os.environ.get("WENMO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))


def tenant_dir(create=False):
    name = tenant_name()
    root = data_root() if name == "local" else os.path.join(data_root(), "users", name)
    if create:
        os.makedirs(root, exist_ok=True)
    return root


def tenant_file(filename):
    safe = os.path.basename(str(filename))
    if safe != filename or not safe:
        raise ValueError("tenant filename must be a simple basename")
    return os.path.join(tenant_dir(False), safe)


def tenant_subdir(name, create=True):
    safe = os.path.basename(str(name))
    if safe != name or not safe:
        raise ValueError("tenant subdirectory must be a simple basename")
    path = os.path.join(tenant_dir(create), safe)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def files_dir(create=True):
    return tenant_subdir("files", create=create)


def apps_dir(create=True):
    return tenant_subdir("apps", create=create)


def resolve_scoped_file(value):
    """Resolve an existing file inside the tenant download area or task workspace."""
    text = str(value or "").strip()
    if not text:
        return None
    roots = [os.path.abspath(files_dir())]
    workspace = str(current_workspace.get() or "").strip()
    if workspace:
        roots.insert(0, os.path.abspath(workspace))
    if text.startswith("/files/"):
        candidates = [os.path.join(files_dir(), os.path.basename(text))]
    elif os.path.isabs(text):
        candidates = [text]
    else:
        candidates = [os.path.join(root, text) for root in roots]
    for candidate in candidates:
        resolved = os.path.abspath(candidate)
        try:
            in_scope = any(os.path.commonpath([resolved, root]) == root for root in roots)
        except ValueError:
            in_scope = False
        if in_scope and os.path.isfile(resolved):
            return resolved
    return None


def load_json(filename, default):
    try:
        with open(tenant_file(filename), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


def atomic_write_json(filename, payload):
    root = tenant_dir(True)
    path = os.path.join(root, os.path.basename(filename))
    temporary = "%s.%s.%s.tmp" % (path, os.getpid(), threading.get_ident())
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path
