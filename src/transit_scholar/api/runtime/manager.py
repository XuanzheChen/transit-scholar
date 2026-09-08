"""Process-local, single-slot AgentRun execution manager."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable, Any


class RunnerBusyError(RuntimeError):
    """Raised when an AgentRun is submitted while another is executing."""


class LocalExecutionManager:
    """Execute persisted AgentRun identities on one disposable worker.

    ``product_factory`` is called in the worker thread, ensuring the worker
    owns its SQLAlchemy session and never retains a request-scoped facade.
    """

    def __init__(self, product_factory: Callable[[], Any], *, max_workers: int = 1):
        if max_workers != 1:
            raise ValueError("local execution manager supports exactly one worker")
        self._product_factory = product_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-run")
        self._lock = Lock()
        self._active_run_id: str | None = None
        self._futures: dict[str, Future] = {}

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    @property
    def is_busy(self) -> bool:
        return self.active_run_id is not None

    def reconcile_interrupted_runs(self) -> list[str]:
        """Pause persisted runs not owned by a worker in this process.

        This is called during application startup only.  It deliberately does
        not submit any work: explicit API resume remains the sole path back to
        execution after a process interruption.
        """
        with self._lock:
            active_run_ids = {self._active_run_id} if self._active_run_id else set()

        product = self._product_factory()
        try:
            return product.reconcile_interrupted_runs(active_run_ids)
        finally:
            if hasattr(product, "close"):
                product.close()

    def submit(self, agent_run_id: str, *, resume: bool = False) -> Future:
        if not isinstance(agent_run_id, str) or not agent_run_id:
            raise ValueError("agent_run_id is required")
        with self._lock:
            if self._active_run_id is not None:
                raise RunnerBusyError("another AgentRun is already executing")
            self._active_run_id = agent_run_id
            future = self._executor.submit(self._run, agent_run_id, resume)
            self._futures[agent_run_id] = future
            return future

    def _run(self, agent_run_id: str, resume: bool = False):
        product = None
        try:
            product = self._product_factory()
            return product.resume_run(agent_run_id) if resume else product.execute_run(agent_run_id)
        finally:
            if product is not None and hasattr(product, "close"):
                product.close()
            with self._lock:
                self._active_run_id = None

    def future(self, agent_run_id: str) -> Future | None:
        with self._lock:
            return self._futures.get(agent_run_id)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)


AgentRunExecutionManager = LocalExecutionManager
LocalAgentRunExecutionManager = LocalExecutionManager
