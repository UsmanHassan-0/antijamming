#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.aj"
PYTHON_BIN="${VENV_DIR}/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing Python environment at ${VENV_DIR}"
  echo "Run ./setup.sh first."
  exit 1
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
cd "${ROOT_DIR}"
export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"

MODE="${1:-unit}"
shift || true

case "${MODE}" in
  unit)
    echo "[run_tests] Running unit/gui tests..."
    exec "${PYTHON_BIN}" -m pytest -q -m "not usrp" "$@"
    ;;
  usrp)
    echo "[run_tests] Running USRP smoke tests marker..."
    exec "${PYTHON_BIN}" -m pytest -q -m usrp "$@"
    ;;
  *)
    echo "Usage: ./run_tests.sh [unit|usrp] [pytest args...]"
    exit 2
    ;;
esac
