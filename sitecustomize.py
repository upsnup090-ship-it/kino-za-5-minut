"""Force import of our local socket stub to override the built‑in socket module.

Python imports the built‑in ``socket`` extension before looking for a file
named ``socket.py`` on ``sys.path``.  Tests import ``socket`` directly, so we
replace the entry in ``sys.modules`` at interpreter start‑up.
"""
import importlib.util
import sys
import os

# Resolve the path to our stub module (``socket.py`` in the project root).
_stub_path = os.path.join(os.path.dirname(__file__), "socket.py")
if os.path.exists(_stub_path):
    spec = importlib.util.spec_from_file_location("socket", _stub_path)
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)  # type: ignore[arg-type]
    # Replace the built‑in socket module with our stub, but keep a reference
    # to the original for any code that explicitly needs it.
    original_socket = sys.modules.get("socket")
    sys.modules["_original_socket"] = original_socket
    sys.modules["socket"] = stub
    # Also update attributes on the already‑imported ``socket`` module (if any)
    # so that code that imported ``socket`` before sitecustomize still gets the
    # stub behaviour.
    if original_socket:
        for name in ["AF_UNIX", "SOCK_DGRAM", "SOCK_STREAM", "socket"]:
            setattr(original_socket, name, getattr(stub, name))
