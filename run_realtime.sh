#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PY="${ROOT_DIR}/.aj/bin/python"
ORIGINAL_ARGS=("$@")
SIDECAR_SCRIPT="${ROOT_DIR}/tools/run_realtime_sidecar.sh"

while (($# > 0)); do
  case "$1" in
    --auto-start|--quit-after-stop|--help|-h)
      shift
      ;;
    --auto-stop-after-s)
      if (($# < 2)); then
        echo "--auto-stop-after-s requires SECONDS." >&2
        exit 2
      fi
      shift 2
      ;;
    *)
      echo "run_realtime.sh only accepts diagnostic control flags:" >&2
      echo "  --auto-start [--auto-stop-after-s SECONDS] [--quit-after-stop]" >&2
      echo "Edit configs/antijamming/x300_realtime.json for runtime configuration." >&2
      exit 2
      ;;
  esac
done

if [[ ! -x "${APP_PY}" ]]; then
  APP_PY="python3"
fi

cd "${ROOT_DIR}"
mkdir -p logs logs/sidecar

export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export MPLBACKEND="${MPLBACKEND:-Agg}"
export UHD_LOG_CONSOLE_LEVEL="${UHD_LOG_CONSOLE_LEVEL:-error}"
export UHD_LOG_FILE="${ROOT_DIR}/logs/uhd_console.log"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export BLIS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

: >"${UHD_LOG_FILE}"

echo "[run_realtime] Launching realtime anti-jamming GUI..."
echo "[run_realtime] Runtime profile: configs/antijamming/x300_realtime.json"
echo "[run_realtime] UHD logs: ${UHD_LOG_FILE}"
echo "[run_realtime] Sidecar logs: logs/sidecar/current (set ANTIJAM_SIDECAR=0 to disable)"
echo "[run_realtime] If the window does not appear, check logs/app.log and logs/errors.log."
"${APP_PY}" -m antijamming.app.main "${ORIGINAL_ARGS[@]}" 2>>"${UHD_LOG_FILE}" &
APP_PID="$!"

SIDECAR_PID=""
if [[ "${ANTIJAM_SIDECAR:-1}" != "0" ]]; then
  if [[ -r "${SIDECAR_SCRIPT}" ]]; then
    ROOT="${ROOT_DIR}" \
      IFACE="${ANTIJAM_SIDECAR_IFACE:-enp6s0f1np1}" \
      INTERVAL="${ANTIJAM_SIDECAR_INTERVAL:-1}" \
      bash "${SIDECAR_SCRIPT}" "${APP_PID}" \
      >"${ROOT_DIR}/logs/sidecar/launcher.out" 2>&1 &
    SIDECAR_PID="$!"
    echo "${SIDECAR_PID}" >"${ROOT_DIR}/logs/sidecar/launcher.pid"
    echo "[run_realtime] Sidecar started: launcher_pid=${SIDECAR_PID} app_pid=${APP_PID}"
  else
    echo "[run_realtime] Sidecar helper missing: ${SIDECAR_SCRIPT}" >&2
  fi
fi

cleanup() {
  local signal="${1:-}"
  if [[ -n "${signal}" ]]; then
    echo "[run_realtime] Received ${signal}; stopping GUI..."
    kill "${APP_PID}" 2>/dev/null || true
  fi
}

trap 'cleanup SIGINT' INT
trap 'cleanup SIGTERM' TERM

set +e
wait "${APP_PID}"
APP_STATUS="$?"
set -e

if [[ -n "${SIDECAR_PID}" ]]; then
  set +e
  wait "${SIDECAR_PID}"
  set -e
fi

exit "${APP_STATUS}"
