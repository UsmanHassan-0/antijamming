"""Runtime backend package exports.

Qt is intentionally imported lazily.  Headless users of :class:`BackendRuntime`
must not load PyQt merely because Python initialized the ``runtime`` package.
"""

# =============================================================================
# Public Runtime API
# =============================================================================

from .backend import BackendRuntime
from .latest_queue import put_latest
from .ui_metrics import RuntimeUiMetrics
from .work_items import PhaseResult, PhaseWorkItem

__all__ = [
    "BackendRuntime",
    "PhaseResult",
    "PhaseWorkItem",
    "RuntimeUiMetrics",
    "put_latest",
]
