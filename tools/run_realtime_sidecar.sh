#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ROOT:-/home/qvise/antijamming}"
cd "${ROOT}"

PY_PID="${1:-}"
IFACE="${IFACE:-enp6s0f1np1}"
INTERVAL="${INTERVAL:-1}"
GNSS_MATCH="${GNSS_MATCH:-${ROOT}/gnss-sdr/*/gnss-sdr --config_file=*fifo_gps_l1.conf}"

if [[ -z "${PY_PID}" ]]; then
  PY_PID="$(pgrep -fo "${ROOT}/.aj/bin/python -m antijamming.app.main" || true)"
fi
if [[ -z "${PY_PID}" ]] || ! kill -0 "${PY_PID}" 2>/dev/null; then
  echo "No running anti-jam GUI process found." >&2
  exit 2
fi

SIDE="logs/sidecar/current"
mkdir -p logs/sidecar
rm -rf "${SIDE}"
mkdir -p "${SIDE}"
printf '%s\n' "${SIDE}" > logs/sidecar/LATEST

CHILD_PIDS=()
GNSS_PID=""

discover_gnss_pid() {
  ps -eo pid=,args= | awk -v root="${ROOT}" '
    index($0, root "/gnss-sdr/") &&
    $0 ~ /\/gnss-sdr --config_file=.*fifo_gps_l1[.]conf/ &&
    $0 !~ /bash -c/ &&
    $0 !~ /tools\/run_realtime_sidecar[.]sh/ {
      gsub(/^[[:space:]]+/, "", $0)
      split($0, fields, " ")
      print fields[1]
      exit
    }
  '
}

pid_list_now() {
  local gnss
  gnss="$(discover_gnss_pid)"
  if [[ -n "${gnss}" ]]; then
    printf '%s,%s\n' "${PY_PID}" "${gnss}"
  else
    printf '%s\n' "${PY_PID}"
  fi
}

write_manifest_start() {
  {
    printf 'sidecar_started_utc=%s\n' "$(date -u --iso-8601=ns)"
    printf 'sidecar_started_local=%s\n' "$(date --iso-8601=ns)"
    printf 'repo=%s\n' "${ROOT}"
    printf 'python_pid=%s\n' "${PY_PID}"
    printf 'gnss_sdr_pid_initial=%s\n' "${GNSS_PID:-missing}"
    printf 'gnss_match=%s\n' "${GNSS_MATCH}"
    printf 'iface=%s\n' "${IFACE}"
    printf 'interval_s=%s\n' "${INTERVAL}"
    printf 'sidecar_pid=%s\n' "$$"
  } > "${SIDE}/manifest.txt"
}

snapshot_processes() {
  local suffix="$1"
  local gnss
  gnss="$(discover_gnss_pid)"
  ps -fp "${PY_PID}" ${gnss:+ "${gnss}"} > "${SIDE}/ps_${suffix}.txt" 2>&1 || true
  ps -L -p "${PY_PID}" ${gnss:+ -p "${gnss}"} \
    -o pid,tid,psr,pcpu,pmem,stat,comm,wchan:32 \
    > "${SIDE}/ps_threads_${suffix}.txt" 2>&1 || true
}

launch() {
  local name="$1"
  shift
  "$@" > "${SIDE}/${name}.log" 2>&1 &
  local pid="$!"
  CHILD_PIDS+=("${pid}")
  printf '%s\n' "${pid}" > "${SIDE}/${name}.pid"
}

GNSS_PID="$(discover_gnss_pid)"
write_manifest_start

uname -a > "${SIDE}/uname.txt" || true
lscpu > "${SIDE}/lscpu.txt" || true
free -h > "${SIDE}/free_start.txt" || true
df -hT > "${SIDE}/df_start.txt" || true
sysctl net.core.rmem_max net.core.rmem_default net.core.wmem_max net.core.wmem_default > "${SIDE}/sysctl_start.txt" 2>&1 || true
ip -d link show "${IFACE}" > "${SIDE}/ip_link_start.txt" 2>&1 || true
ip -s link show "${IFACE}" > "${SIDE}/ip_stats_start.txt" 2>&1 || true
ethtool "${IFACE}" > "${SIDE}/ethtool_start.txt" 2>&1 || true
ethtool -g "${IFACE}" > "${SIDE}/ethtool_ring_start.txt" 2>&1 || true
ethtool -S "${IFACE}" > "${SIDE}/ethtool_stats_start.txt" 2>&1 || true
snapshot_processes start

launch pidstat_antijam_gnss bash -c '
root_pid="$0"
repo_root="$1"
interval="$2"
last=""
while kill -0 "${root_pid}" 2>/dev/null; do
  gnss="$(ps -eo pid=,args= | awk -v root="${repo_root}" '\''index($0, root "/gnss-sdr/") && $0 ~ /\/gnss-sdr --config_file=.*fifo_gps_l1[.]conf/ && $0 !~ /bash -c/ && $0 !~ /tools\/run_realtime_sidecar[.]sh/ {gsub(/^[[:space:]]+/, "", $0); split($0, fields, " "); print fields[1]; exit}'\'')"
  pids="${root_pid}"
  if [[ -n "${gnss}" ]]; then
    pids="${pids},${gnss}"
  fi
  if [[ "${pids}" != "${last}" ]]; then
    printf "### %s pids=%s\n" "$(date --iso-8601=ns)" "${pids}"
    last="${pids}"
  fi
  pidstat -durh -p "${pids}" "${interval}" 1
done
' "${PY_PID}" "${ROOT}" "${INTERVAL}"

launch pidstat_threads_antijam_gnss bash -c '
root_pid="$0"
repo_root="$1"
interval="$2"
last=""
while kill -0 "${root_pid}" 2>/dev/null; do
  gnss="$(ps -eo pid=,args= | awk -v root="${repo_root}" '\''index($0, root "/gnss-sdr/") && $0 ~ /\/gnss-sdr --config_file=.*fifo_gps_l1[.]conf/ && $0 !~ /bash -c/ && $0 !~ /tools\/run_realtime_sidecar[.]sh/ {gsub(/^[[:space:]]+/, "", $0); split($0, fields, " "); print fields[1]; exit}'\'')"
  pids="${root_pid}"
  if [[ -n "${gnss}" ]]; then
    pids="${pids},${gnss}"
  fi
  if [[ "${pids}" != "${last}" ]]; then
    printf "### %s pids=%s\n" "$(date --iso-8601=ns)" "${pids}"
    last="${pids}"
  fi
  pidstat -t -durh -p "${pids}" "${interval}" 1
done
' "${PY_PID}" "${ROOT}" "${INTERVAL}"

launch gnss_pid_watch bash -c '
root_pid="$0"
repo_root="$1"
interval="$2"
last=""
while kill -0 "${root_pid}" 2>/dev/null; do
  gnss="$(ps -eo pid=,args= | awk -v root="${repo_root}" '\''index($0, root "/gnss-sdr/") && $0 ~ /\/gnss-sdr --config_file=.*fifo_gps_l1[.]conf/ && $0 !~ /bash -c/ && $0 !~ /tools\/run_realtime_sidecar[.]sh/ {gsub(/^[[:space:]]+/, "", $0); split($0, fields, " "); print fields[1]; exit}'\'')"
  if [[ "${gnss}" != "${last}" ]]; then
    printf "%s gnss_sdr_pid=%s\n" "$(date --iso-8601=ns)" "${gnss:-missing}"
    last="${gnss}"
  fi
  sleep "${interval}"
done
' "${PY_PID}" "${ROOT}" "${INTERVAL}"

launch mpstat_per_core mpstat -P ALL "${INTERVAL}"
launch iostat_disk iostat -xz "${INTERVAL}"
launch vmstat vmstat -t "${INTERVAL}"

launch top_threads bash -c '
interval="$0"
while true; do
  date --iso-8601=ns
  ps -eLo pid,tid,psr,pcpu,pmem,stat,comm,wchan:32,args --sort=-pcpu | head -160
  sleep "${interval}"
done
' "${INTERVAL}"

launch proc_pressure bash -c '
interval="$0"
while true; do
  date --iso-8601=ns
  for f in /proc/pressure/cpu /proc/pressure/io /proc/pressure/memory; do
    echo "### ${f}"
    sed -n "1,5p" "${f}" 2>/dev/null || true
  done
  sleep "${interval}"
done
' "${INTERVAL}"

launch meminfo_writeback bash -c '
interval="$0"
while true; do
  date --iso-8601=ns
  egrep "Dirty:|Writeback:|WritebackTmp:|MemAvailable:|SwapFree:" /proc/meminfo
  sleep "${interval}"
done
' "${INTERVAL}"

launch softnet_stat bash -c '
interval="$0"
while true; do
  date --iso-8601=ns
  sed -n "1,256p" /proc/net/softnet_stat
  sleep "${interval}"
done
' "${INTERVAL}"

launch nic_stats bash -c '
iface="$0"
interval="$1"
while true; do
  date --iso-8601=ns
  ip -s link show "${iface}"
  ethtool -S "${iface}" 2>/dev/null | egrep -i "rx|tx|drop|err|miss|timeout|buf|fifo|crc|over|disc|no" || true
  sleep "${interval}"
done
' "${IFACE}" "${INTERVAL}"

cleanup() {
  {
    printf 'sidecar_stopping_utc=%s\n' "$(date -u --iso-8601=ns)"
    printf 'sidecar_stopping_local=%s\n' "$(date --iso-8601=ns)"
    printf 'gnss_sdr_pid_final=%s\n' "$(discover_gnss_pid || true)"
  } >> "${SIDE}/manifest.txt"

  for pid in "${CHILD_PIDS[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
  sleep 0.5
  for pid in "${CHILD_PIDS[@]}"; do
    kill -9 "${pid}" 2>/dev/null || true
  done

  free -h > "${SIDE}/free_end.txt" 2>&1 || true
  df -hT > "${SIDE}/df_end.txt" 2>&1 || true
  sysctl net.core.rmem_max net.core.rmem_default net.core.wmem_max net.core.wmem_default > "${SIDE}/sysctl_end.txt" 2>&1 || true
  ip -s link show "${IFACE}" > "${SIDE}/ip_stats_end.txt" 2>&1 || true
  ethtool -S "${IFACE}" > "${SIDE}/ethtool_stats_end.txt" 2>&1 || true
  snapshot_processes end

  {
    printf 'sidecar_stopped_utc=%s\n' "$(date -u --iso-8601=ns)"
    printf 'sidecar_stopped_local=%s\n' "$(date --iso-8601=ns)"
  } >> "${SIDE}/manifest.txt"
}

trap cleanup EXIT INT TERM

while kill -0 "${PY_PID}" 2>/dev/null; do
  sleep 1
done
