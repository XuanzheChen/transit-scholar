from __future__ import annotations

from threading import Event
from time import monotonic

import pytest

from transit_scholar.api.runtime import LocalExecutionManager, RunnerBusyError


class FakeProduct:
    def __init__(self, started: Event, release: Event):
        self.started = started
        self.release = release
        self.closed = False

    def execute_run(self, run_id):
        self.started.set()
        self.release.wait(timeout=2)
        return run_id

    def close(self):
        self.closed = True


class PauseAwareProduct(FakeProduct):
    def __init__(self, started: Event, release: Event):
        super().__init__(started, release)
        self.pause_requests = []

    def request_pause(self, run_id):
        self.pause_requests.append(run_id)
        self.release.set()


def test_only_one_agent_run_executes_at_a_time():
    started = Event()
    release = Event()
    products = []

    def factory():
        product = FakeProduct(started, release)
        products.append(product)
        return product

    manager = LocalExecutionManager(factory)
    try:
        first = manager.submit("run-1")
        assert started.wait(timeout=1)
        with pytest.raises(RunnerBusyError):
            manager.submit("run-2")
        release.set()
        assert first.result(timeout=2) == "run-1"
        assert products[0].closed
    finally:
        manager.shutdown()


def test_executor_submission_failure_releases_slot_for_later_submission(monkeypatch):
    release = Event()
    release.set()
    manager = LocalExecutionManager(lambda: FakeProduct(Event(), release))
    try:
        reservation = manager.reserve()
        assert manager.is_busy
        with pytest.raises(RunnerBusyError):
            manager.reserve()
        reservation.release()
        assert not manager.is_busy

        original_submit = manager._executor.submit
        monkeypatch.setattr(
            manager._executor,
            "submit",
            lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")),
        )
        with pytest.raises(RuntimeError, match="unavailable"):
            manager.submit("run-1")
        assert not manager.is_busy
        monkeypatch.setattr(manager._executor, "submit", original_submit)
        assert manager.submit("run-2").result(timeout=2) == "run-2"
    finally:
        manager.shutdown()


def test_worker_uses_fresh_product_after_request_scope_closes():
    created = []
    request_product = object()
    worker_product = FakeProduct(Event(), Event())
    worker_product.release.set()

    def factory():
        created.append(True)
        return worker_product

    manager = LocalExecutionManager(factory)
    try:
        del request_product
        result = manager.submit("persisted-run").result(timeout=2)
        assert result == "persisted-run"
        assert len(created) == 1
        assert worker_product.closed
    finally:
        manager.shutdown()


def test_completed_futures_are_removed_and_slot_is_reusable():
    release = Event()
    release.set()
    manager = LocalExecutionManager(lambda: FakeProduct(Event(), release))
    try:
        first = manager.submit("run-1")
        assert first.result(timeout=2) == "run-1"
        assert manager.future("run-1") is None
        assert not manager.is_busy

        second = manager.submit("run-2")
        assert second.result(timeout=2) == "run-2"
        assert manager.future("run-2") is None
        assert not manager.is_busy
    finally:
        manager.shutdown()


def test_invalid_submission_releases_reservation():
    manager = LocalExecutionManager(lambda: FakeProduct(Event(), Event()))
    try:
        with pytest.raises(ValueError):
            manager.submit("")
        assert not manager.is_busy
    finally:
        manager.shutdown()


def test_shutdown_requests_pause_and_releases_local_ownership():
    started = Event()
    release = Event()
    products = []

    def factory():
        product = PauseAwareProduct(started, release)
        products.append(product)
        return product

    manager = LocalExecutionManager(factory)
    manager.submit("run-1")
    assert started.wait(timeout=1)

    manager.shutdown(checkpoint_timeout=0.5)

    assert products[1].pause_requests == ["run-1"]
    assert products[1].closed
    assert not manager.is_busy
    assert manager.future("run-1") is None


def test_shutdown_does_not_wait_indefinitely_for_uncooperative_run():
    started = Event()
    release = Event()
    manager = LocalExecutionManager(lambda: FakeProduct(started, release))
    manager.submit("run-1")
    assert started.wait(timeout=1)

    began = monotonic()
    manager.shutdown(checkpoint_timeout=0.01)

    assert monotonic() - began < 0.5
    assert manager.closed
    assert manager.is_busy
    assert manager.active_run_id == "run-1"
    with pytest.raises(RunnerBusyError):
        manager.reserve()
    with pytest.raises(RunnerBusyError):
        manager.submit("run-2")
    release.set()
    for _ in range(20):
        if not manager.is_busy:
            break
        Event().wait(0.01)
    assert not manager.is_busy
    assert manager.active_run_id is None
    assert manager.future("run-1") is None
    assert manager.closed

def test_shutdown_with_outstanding_reservation_and_concurrent_admission():
    from threading import Thread
    manager = LocalExecutionManager(lambda: FakeProduct(Event(), Event()))
    reservation = manager.reserve()
    finished = Event()
    def shutdown():
        manager.shutdown(checkpoint_timeout=0.01)
        finished.set()
    worker = Thread(target=shutdown, daemon=True)
    worker.start()
    assert finished.wait(1), 'shutdown deadlocked with an outstanding reservation'
    assert manager.closed
    with pytest.raises(RunnerBusyError):
        manager.reserve()
    released = Event()
    def release():
        reservation.release()
        released.set()
    Thread(target=release, daemon=True).start()
    assert released.wait(1), 'reservation release deadlocked after shutdown'
    assert not manager.is_busy

def test_closed_admission_rejected_while_shutdown_is_still_running(monkeypatch):
    from threading import Thread
    manager = LocalExecutionManager(lambda: FakeProduct(Event(), Event()))
    entered = Event()
    release = Event()
    original = manager._executor.shutdown
    def delayed_shutdown(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager._executor, 'shutdown', delayed_shutdown)
    worker = Thread(target=manager.shutdown, daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        assert worker.is_alive() and manager.closed
        with pytest.raises(RunnerBusyError):
            manager.reserve()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
