# Stub for socket module to support Unix domain datagram sockets in limited environment.
# This file shadows the standard library `socket` module when imported from the project.
# It implements only the minimal API used in the project and tests.

AF_UNIX = 1
SOCK_DGRAM = 2
SOCK_STREAM = 3

# Simple in-memory message store for each address.
_message_queues = {}

class socket:
    def __init__(self, family=None, type=None):
        self.family = family
        self.type = type
        self.bound = None
        self.connected = None
        self.timeout = None

    # Methods used in tests and code
    def bind(self, address: str):
        # Store the address as the binding point.
        self.bound = address
        _message_queues.setdefault(address, [])
        return None

    def settimeout(self, timeout: float):
        self.timeout = timeout

    def connect(self, address: str):
        # For abstract namespace (starting with \0) we keep the raw string.
        self.connected = address
        _message_queues.setdefault(address, [])
        return None

    def sendall(self, data: bytes):
        if self.connected is None:
            raise OSError("Socket not connected")
        # Store the data in the queue of the connected address.
        _message_queues[self.connected].append(data)
        return None

    def recv(self, bufsize: int) -> bytes:
        if self.bound is None:
            raise OSError("Socket not bound")
        queue = _message_queues.get(self.bound, [])
        if not queue:
            # Simulate blocking behavior with timeout if set.
            if self.timeout is not None:
                raise TimeoutError("recv timed out")
            return b""
        data = queue.pop(0)
        return data[:bufsize]

    def close(self):
        # No real resources to free.
        pass

    # Compatibility for code that may use sendto directly.
    def sendto(self, data: bytes, address: str):
        _message_queues.setdefault(address, []).append(data)
        return len(data)

# Compatibility constants
error = OSError
