"""Request/task-local execution scope propagated into worker threads."""

from contextvars import ContextVar
from contextlib import contextmanager


current_task_id = ContextVar("wenmo_task_id", default="")
current_workspace = ContextVar("wenmo_workspace", default="")
current_tenant = ContextVar("wenmo_tenant", default="local")


@contextmanager
def task_execution_context(task_id="", workspace="", tenant="local"):
    tokens = (
        (current_task_id, current_task_id.set(str(task_id or ""))),
        (current_workspace, current_workspace.set(str(workspace or ""))),
        (current_tenant, current_tenant.set(str(tenant or "local"))),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
