from __future__ import annotations

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def collect_deleted_qt_graphics(request: pytest.FixtureRequest):
    """Finish deferred Qt deletion before Python collects graphics wrappers.

    PyQtGraph scenes contain Python-backed QGraphicsItem subclasses. Under
    coverage instrumentation, garbage collection can otherwise overlap a
    queued QGraphicsScene bounds update during the next GUI test and invoke a
    half-destroyed item's virtual method.
    """

    yield
    if "qtbot" not in request.fixturenames:
        return
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.processEvents()
