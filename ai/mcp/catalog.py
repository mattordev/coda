"""Runtime catalog discovery with independent optional-server failures."""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from ai.mcp.runtime import LocalMCPManager


class MCPRuntimeCatalog:
    """Load without connecting; discover trusted servers on an external lookup."""

    def __init__(self, manager=None):
        if manager is None:
            self.manager, self.errors = LocalMCPManager.from_directory()
        else:
            self.manager, self.errors = manager, {}
        self._lock = Lock()
        self._attempted = set()
        self._intents = {}

    def discover(self, *, eager_only=False, cancel_event=None):
        with self._lock:
            servers = [
                name for name, runtime in self.manager._servers.items()
                if runtime.definition.trusted and name not in self._attempted
                and (not eager_only or runtime.definition.startup_mode == "eager")
            ]
            if servers:
                with ThreadPoolExecutor(max_workers=min(len(servers), 8), thread_name_prefix="mcp-discovery") as pool:
                    futures = {name: pool.submit(self.manager.discover, name, cancel_event=cancel_event) for name in servers}
                    for name, future in futures.items():
                        result = future.result()
                        if cancel_event is not None and cancel_event.is_set():
                            continue
                        self._attempted.add(name)
                        if result.success:
                            self._intents[name] = result.data
                        else:
                            self.errors[name] = result.error
            return tuple(intent for values in self._intents.values() for intent in values)

    def close(self, *, timeout_seconds=10):
        return self.manager.close(timeout_seconds=timeout_seconds)
