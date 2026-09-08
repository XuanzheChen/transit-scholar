"""Process-local, single-slot AgentRun execution manager."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Any


class RunnerBusyError(RuntimeError):
    """Raised when an AgentRun is submitted while another is executing."""


@dataclass(frozen=True)
class ExecutionReservation:
    """Exclusive admission ownership held before an AgentRun is persisted."""

    manager: "LocalExecutionManager"
    token: object

    def release(self) -> None:
        self.manager.release_reservation(self)


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
        self._reservation: ExecutionReservation | None = None
        self._futures: dict[str, Future] = {}

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._active_run_id is not None or self._reservation is not None

    def reserve(self) -> ExecutionReservation:
        """Atomically claim the execution slot before Product mutation."""
        with self._lock:
            if self._active_run_id is not None or self._reservation is not None:
                raise RunnerBusyError("another AgentRun is already executing")
            reservation = ExecutionReservation(self, object())
            self._reservation = reservation
            return reservation

    def release_reservation(self, reservation: ExecutionReservation) -> None:
        with self._lock:
            if self._reservation is reservation:
                self._reservation = None

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
        reservation = self.reserve()
        try:
            return self.submit_reserved(reservation, agent_run_id, resume=resume)
        except Exception:
            reservation.release()
            raise

    def submit_reserved(
        self,
        reservation: ExecutionReservation,
        agent_run_id: str,
        *,
        resume: bool = False,
    ) -> Future:
        if not isinstance(agent_run_id, str) or not agent_run_id:
            reservation.release()
            raise ValueError("agent_run_id is required")
        with self._lock:
            if self._reservation is not reservation:
                raise RuntimeError("execution reservation is not active")
            self._reservation = None
            self._active_run_id = agent_run_id
        try:
            future = self._executor.submit(self._run, agent_run_id, resume)
        except Exception:
            with self._lock:
                if self._active_run_id == agent_run_id:
                    self._active_run_id = None
            raise
        try:
            with self._lock:
                self._futures[agent_run_id] = future
            future.add_done_callback(lambda completed: self._release(agent_run_id, completed))
        except Exception:
            with self._lock:
                if self._futures.get(agent_run_id) is future:
                    self._futures.pop(agent_run_id, None)
                if self._active_run_id == agent_run_id:
                    self._active_run_id = None
            future.cancel()
            raise
        return future

    def _release(self, agent_run_id: str, future: Future) -> None:
        with self._lock:
            if self._futures.get(agent_run_id) is future:
                self._futures.pop(agent_run_id, None)
            if self._active_run_id == agent_run_id:
                self._active_run_id = None

    def _run(self, agent_run_id: str, resume: bool = False):
        product = None
        try:
            product = self._product_factory()
            return product.resume_run(agent_run_id) if resume else product.execute_run(agent_run_id)
        finally:
            if product is not None and hasattr(product, "close"):
                product.close()

    def future(self, agent_run_id: str) -> Future | None:
        with self._lock:
            return self._futures.get(agent_run_id)

    def shutdown(self, wait: bool = True, *, checkpoint_timeout: float = 1.0) -> None:
        """Request a durable pause, then detach process-local worker state.

        A running provider call cannot safely be force-cancelled.  Give it a
        bounded opportunity to observe the pause request, but never make API
        shutdown wait indefinitely for it.
        """
        with self._lock:
            active_run_id = self._active_run_id
            active_future = self._futures.get(active_run_id) if active_run_id else None

        if active_run_id is not None:
            product = None
            try:
                product = self._product_factory()
                request_pause = getattr(product, "request_pause", None)
                if callable(request_pause):
                    request_pause(active_run_id)
            except Exception:
                # Shutdown is best effort: a worker may have just completed,
                # or its product scope may no longer be available.
                pass
            finally:
                if product is not None and hasattr(product, "close"):
                    product.close()

        if wait and active_future is not None:
            try:
                active_future.result(timeout=max(0.0, checkpoint_timeout))
            except TimeoutError:
                pass
            except Exception:
                pass

        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._active_run_id = None
            self._reservation = None
            self._futures.clear()


AgentRunExecutionManager = LocalExecutionManager
LocalAgentRunExecutionManager = LocalExecutionManager
