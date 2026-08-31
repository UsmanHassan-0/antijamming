#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PY="${ROOT_DIR}/.aj/bin/python"
ORIGINAL_ARGS=("$@")
SIDECAR_SCRIPT="${ROOT_DIR}/tools/run_realtime_sidecar.sh"
RUNTIME_CONFIG="${ROOT_DIR}/configs/antijamming/x300_realtime.json"
GNSS_CONFIG_PATH="${ROOT_DIR}/logs/gnss-sdr/runtime/fifo_gps_l1.conf"

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
mkdir -p logs

RUNTIME_LOGGING_ENABLED="$("${APP_PY}" - <<'PY'
import json
from pathlib import Path

value = json.loads(Path("configs/antijamming/x300_realtime.json").read_text()).get(
    "logging_enabled"
)
if type(value) is not bool:
    raise SystemExit("logging_enabled must be a JSON boolean")
print("1" if value else "0")
PY
)"

runtime_usrp_ip() {
  "${APP_PY}" - <<'PY'
import json
import re
from pathlib import Path

cfg = json.loads(Path("configs/antijamming/x300_realtime.json").read_text())
match = re.search(r"addr=([\d.]+)", str(cfg.get("usrp_addr", "")))
if match:
    print(match.group(1))
PY
}

route_iface_for_ip() {
  local ip_addr="$1"
  local route_line

  [[ -n "${ip_addr}" ]] || return 1
  route_line="$(ip route get "${ip_addr}" 2>/dev/null | head -n1 || true)"
  awk '
    {
      for (i = 1; i <= NF; i++) {
        if ($i == "dev" && (i + 1) <= NF) {
          print $(i + 1)
          exit
        }
      }
    }
  ' <<<"${route_line}"
}

qt_platform_name() {
  printf '%s\n' "${QT_QPA_PLATFORM%%:*}"
}

owned_gnss_sdr_pids() {
  local backend_pids=()
  local backend_pid
  local backend_pgid

  # The config path is shared by standalone and Tramiq-owned services, so it
  # is not an ownership boundary. Only select GNSS-SDR inside this wrapper's
  # socket-specific headless process group.
  mapfile -t backend_pids < <(owned_headless_backend_pids)
  for backend_pid in "${backend_pids[@]}"; do
    backend_pgid="$(ps -o pgid= -p "${backend_pid}" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "${backend_pgid}" ]] || continue
    ps -eo pid=,pgid=,args= | awk \
      -v owned_pgid="${backend_pgid}" \
      -v config_path="${GNSS_CONFIG_PATH}" '
        $2 == owned_pgid && index($0, config_path) && $0 !~ /awk -v/ {
          print $1
        }
      '
  done | sort -u
}

stop_owned_gnss_sdr() {
  local pids=()

  mapfile -t pids < <(owned_gnss_sdr_pids)
  if ((${#pids[@]} == 0)); then
    return 0
  fi

  echo "[run_realtime] Stopping owned GNSS-SDR process(es): ${pids[*]}"
  kill -TERM "${pids[@]}" 2>/dev/null || true
  for _ in {1..50}; do
    mapfile -t pids < <(owned_gnss_sdr_pids)
    if ((${#pids[@]} == 0)); then
      return 0
    fi
    sleep 0.1
  done

  echo "[run_realtime] GNSS-SDR did not stop after SIGTERM; sending SIGKILL: ${pids[*]}" >&2
  kill -KILL "${pids[@]}" 2>/dev/null || true
}

owned_headless_backend_pids() {
  local socket_path="/tmp/antijamming-gui-${APP_PID}.sock"

  ps -eo pid=,args= | awk -v socket_path="${socket_path}" '
    index($0, "-m antijamming.app.headless") &&
    index($0, "--socket " socket_path) &&
    $0 !~ /awk -v/ {
      gsub(/^[[:space:]]+/, "", $0)
      split($0, fields, " ")
      print fields[1]
    }
  '
}

stop_owned_headless_backend() {
  local pids=()
  local pid
  local pgid
  local socket_path="/tmp/antijamming-gui-${APP_PID}.sock"

  mapfile -t pids < <(owned_headless_backend_pids)
  if ((${#pids[@]} > 0)); then
    echo "[run_realtime] Stopping owned headless backend: ${pids[*]}"
    for pid in "${pids[@]}"; do
      pgid="$(ps -o pgid= -p "${pid}" 2>/dev/null | tr -d '[:space:]')"
      if [[ "${pgid}" == "${pid}" ]]; then
        kill -TERM -- "-${pgid}" 2>/dev/null || true
      else
        kill -TERM "${pid}" 2>/dev/null || true
      fi
    done
    for _ in {1..50}; do
      mapfile -t pids < <(owned_headless_backend_pids)
      if ((${#pids[@]} == 0)); then
        break
      fi
      sleep 0.1
    done
  fi

  if ((${#pids[@]} > 0)); then
    echo "[run_realtime] Headless backend did not stop after SIGTERM; sending SIGKILL: ${pids[*]}" >&2
    for pid in "${pids[@]}"; do
      pgid="$(ps -o pgid= -p "${pid}" 2>/dev/null | tr -d '[:space:]')"
      if [[ "${pgid}" == "${pid}" ]]; then
        kill -KILL -- "-${pgid}" 2>/dev/null || true
      else
        kill -KILL "${pid}" 2>/dev/null || true
      fi
    done
  fi
  if [[ -S "${socket_path}" ]] && ((${#pids[@]} == 0)); then
    rm -f -- "${socket_path}"
  fi
}

export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"
if [[ -z "${QT_QPA_PLATFORM:-}" && -n "${WAYLAND_DISPLAY:-}" ]]; then
  export QT_QPA_PLATFORM="wayland"
else
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
fi
export MPLBACKEND="${MPLBACKEND:-Agg}"
export UHD_LOG_CONSOLE_LEVEL="${UHD_LOG_CONSOLE_LEVEL:-error}"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export BLIS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export ANTIJAM_GNSS_STARTUP_CONSOLE="${ANTIJAM_GNSS_STARTUP_CONSOLE:-1}"

case "$(qt_platform_name)" in
  offscreen|minimal)
    ;;
  wayland)
    if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
      echo "[run_realtime] QT_QPA_PLATFORM=wayland but WAYLAND_DISPLAY is not set." >&2
      exit 2
    fi
    ;;
  *)
    if [[ -z "${DISPLAY:-}" ]]; then
      echo "[run_realtime] No X11 display is available in this shell." >&2
      echo "[run_realtime] Use the configured GNOME RDP desktop at 10.189.184.209:3389, then run from inside that desktop." >&2
      exit 2
    fi
    ;;
esac

if [[ "${RUNTIME_LOGGING_ENABLED}" == "1" ]]; then
  export UHD_LOG_FILE="${ROOT_DIR}/logs/uhd_console.log"
  mkdir -p logs/sidecar
  : >"${UHD_LOG_FILE}"
else
  unset UHD_LOG_FILE
fi

echo "[run_realtime] Launching realtime anti-jamming GUI..."
echo "[run_realtime] Runtime profile: ${RUNTIME_CONFIG#${ROOT_DIR}/}"
if [[ "${RUNTIME_LOGGING_ENABLED}" == "1" ]]; then
  echo "[run_realtime] Logging: enabled (UHD=${UHD_LOG_FILE}, sidecar=logs/sidecar/current)"
else
  echo "[run_realtime] Logging: disabled by x300_realtime.json (diagnostic sidecar off)"
fi
echo "[run_realtime] Qt platform: ${QT_QPA_PLATFORM}"
case "$(qt_platform_name)" in
  offscreen|minimal)
    echo "[run_realtime] Display target: ${QT_QPA_PLATFORM} (DISPLAY ignored: ${DISPLAY:-unset})"
    ;;
  *)
    echo "[run_realtime] Display target: ${DISPLAY:-${WAYLAND_DISPLAY:-unknown}}"
    ;;
esac
echo "[run_realtime] If the window does not appear, check logs/app.log and logs/errors.log."
if [[ "${RUNTIME_LOGGING_ENABLED}" == "1" ]]; then
  "${APP_PY}" -m antijamming.app.main "${ORIGINAL_ARGS[@]}" 2>>"${UHD_LOG_FILE}" &
else
  "${APP_PY}" -m antijamming.app.main "${ORIGINAL_ARGS[@]}" &
fi
APP_PID="$!"

SIDECAR_PID=""
if [[ "${RUNTIME_LOGGING_ENABLED}" == "1" ]]; then
  if [[ -r "${SIDECAR_SCRIPT}" ]]; then
    SIDECAR_IFACE="${ANTIJAM_SIDECAR_IFACE:-}"
    if [[ -z "${SIDECAR_IFACE}" ]]; then
      SIDECAR_IFACE="$(route_iface_for_ip "$(runtime_usrp_ip)")"
    fi
    ROOT="${ROOT_DIR}" \
      IFACE="${SIDECAR_IFACE}" \
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
  stop_owned_gnss_sdr
}

trap 'cleanup SIGINT' INT
trap 'cleanup SIGTERM' TERM

set +e
wait "${APP_PID}"
APP_STATUS="$?"
set -e

stop_owned_headless_backend
stop_owned_gnss_sdr

if [[ -n "${SIDECAR_PID}" ]]; then
  set +e
  wait "${SIDECAR_PID}"
  set -e
fi

exit "${APP_STATUS}"
