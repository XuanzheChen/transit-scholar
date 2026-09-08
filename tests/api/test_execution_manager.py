from __future__ import annotations

from threading import Event

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
