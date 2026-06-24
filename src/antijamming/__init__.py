"""Anti-jamming GNSS receiver product package."""

from __future__ import annotations

import os


# The DSP pipeline already parallelizes independent stages. Allowing each small
# NumPy/SciPy operation to create a full 32-thread BLAS team starves GNSS-SDR
# during acquisition bursts and is slower for the matrices used here.
for _thread_env in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_thread_env] = "1"
