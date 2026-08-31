from __future__ import annotations

import numpy as np
import pytest

from antijamming.radio.usrp import RxChunkResult
from tools.usrp_source_count_snapshot import receive_snapshot


class _SnapshotDevice:
    def __init__(self, results: list[RxChunkResult]) -> None:
        self._results = iter(results)

    def recv_chunk(self) -> RxChunkResult:
        return next(self._results)

    def restart_stream(self) -> None:
        raise AssertionError("restart was not expected")


def _result(*, state: str = "ok", error_code: str = "none") -> RxChunkResult:
    return RxChunkResult(
        chunk=np.ones((4, 8), dtype=np.complex64),
        state=state,  # type: ignore[arg-type]
        got_samples=8,
        error_code=error_code,
        out_of_sequence=False,
        time_spec_s=None,
    )


def test_snapshot_consumer_uses_typed_rx_result_contract() -> None:
    raw, states, _elapsed = receive_snapshot(
        _SnapshotDevice([_result()]),  # type: ignore[arg-type]
        chunks=1,
        expected_channels=4,
    )

    assert raw.shape == (4, 8)
    assert raw.dtype == np.dtype(np.complex64)
    assert states == {"ok": 1}


def test_snapshot_consumer_rejects_unknown_metadata_before_using_iq() -> None:
    with pytest.raises(RuntimeError, match="unsupported RX metadata.*alignment"):
        receive_snapshot(
            _SnapshotDevice([_result(state="other", error_code="alignment")]),  # type: ignore[arg-type]
            chunks=1,
            expected_channels=4,
        )
