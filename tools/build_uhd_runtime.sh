#!/usr/bin/env bash
set -euo pipefail

UHD_TAG="v4.10.0.0"
UHD_COMMIT="2af4ddb96219a99d2300804830e0971f79557b23"
UHD_REPOSITORY="https://github.com/EttusResearch/uhd.git"
UHD_SOURCE_DIR="${ANTIJAMMING_UHD_SOURCE_DIR:-${HOME}/uhd-official-4.10.0.0}"
UHD_BUILD_DIR="${ANTIJAMMING_UHD_BUILD_DIR:-${UHD_SOURCE_DIR}/build-host}"
UHD_INSTALL_PREFIX="${ANTIJAMMING_UHD_PREFIX:-${UHD_SOURCE_DIR}/install-host}"
BUILD_JOBS="${ANTIJAMMING_BUILD_JOBS:-$(nproc)}"

if [[ ! -e "${UHD_SOURCE_DIR}" ]]; then
  git clone --branch "${UHD_TAG}" --depth 1 "${UHD_REPOSITORY}" "${UHD_SOURCE_DIR}"
elif [[ ! -d "${UHD_SOURCE_DIR}/.git" ]]; then
  echo "Refusing to replace non-Git path at ${UHD_SOURCE_DIR}." >&2
  exit 1
fi

actual_commit="$(git -C "${UHD_SOURCE_DIR}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${UHD_COMMIT}" ]]; then
  echo "Unexpected UHD source commit at ${UHD_SOURCE_DIR}." >&2
  echo "Expected ${UHD_COMMIT} (${UHD_TAG}); found ${actual_commit}." >&2
  exit 1
fi

cmake \
  -S "${UHD_SOURCE_DIR}/host" \
  -B "${UHD_BUILD_DIR}" \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${UHD_INSTALL_PREFIX}" \
  -DENABLE_PYTHON_API=ON \
  -DENABLE_TESTS=OFF \
  -DENABLE_EXAMPLES=ON \
  -DENABLE_UTILS=ON
cmake --build "${UHD_BUILD_DIR}" --target install -j"${BUILD_JOBS}"

# shellcheck source=tools/uhd_runtime_env.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/uhd_runtime_env.sh"
ANTIJAMMING_UHD_PREFIX="${UHD_INSTALL_PREFIX}" \
  antijamming_activate_uhd_runtime "${PYTHON_BIN:-python3}"
