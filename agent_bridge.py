"""In-process bridge for the agent plugin.

Avoids a loopback HTTP request, which would lose request ContextVars such as the
authenticated tenant and conversation id.
"""

import threading


_lock = threading.Lock()
_delegate = None


def register_delegate(callback):
    global _delegate
    with _lock:
        _delegate = callback


def delegate(arguments):
    with _lock:
        callback = _delegate
    if callback is None:
        return {"error": "agent runtime is not ready"}
    return callback(arguments)
