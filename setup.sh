#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GNSS_SRC_DIR="${ROOT_DIR}/gnss-sdr"
GNSS_BUILD_DIR="${GNSS_SRC_DIR}/build-antijamming"
GNSS_INSTALL_DIR="${GNSS_SRC_DIR}/install"
GNSS_BUILD_BIN="${GNSS_BUILD_DIR}/src/main/gnss-sdr"
GNSS_VOLK_PROFILE_BIN="${GNSS_INSTALL_DIR}/volk_gnsssdr_profile"
VOLK_CONFIG_FILE="${HOME}/.volk/volk_config"
GNSS_VOLK_CONFIG_FILE="${HOME}/.volk_gnsssdr/volk_gnsssdr_config"
PHASE_CALIBRATION_FILE="${ROOT_DIR}/configs/calibration/x300_phase_offsets_100khz.json"
UHD_IMAGE_DIR="${UHD_IMAGE_DIR:-/usr/share/uhd/images}"
USRP_ADDR="${ANTIJAMMING_USRP_ADDR:-}"
USRP_IFACE="${ANTIJAMMING_USRP_IFACE:-}"
USRP_NM_PROFILE="${ANTIJAMMING_USRP_NM_PROFILE:-usrp-x300}"
USRP_HOST_CIDR="${ANTIJAMMING_USRP_HOST_CIDR:-}"
USRP_ADDR_CANDIDATES="${ANTIJAMMING_USRP_ADDR_CANDIDATES:-192.168.30.2 192.168.40.2 192.168.10.2 192.168.20.2}"
USRP_MTU="${ANTIJAMMING_USRP_MTU:-9000}"
USRP_SOCKET_BUFFER_BYTES="${ANTIJAMMING_USRP_SOCKET_BUFFER_BYTES:-50000000}"
VENV_DIR="${ROOT_DIR}/.aj"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNTIME_CONFIG="${ROOT_DIR}/configs/antijamming/x300_realtime.json"

cd "${ROOT_DIR}"

version_ge() {
  local current="$1"
  local required="$2"

  [[ "$(printf '%s\n' "${required}" "${current}" | sort -V | head -n1)" == "${required}" ]]
}

run_privileged() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  elif [[ -n "${ANTIJAMMING_SUDO_PASSWORD:-}" ]]; then
    printf '%s\n' "${ANTIJAMMING_SUDO_PASSWORD}" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

select_gnss_packages() {
  local os_id="unknown"
  local version_id="unknown"
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    os_id="${ID:-unknown}"
    version_id="${VERSION_ID:-unknown}"
  fi

  # Keep these package sets aligned with the vendored gnss-sdr/README.md.
  local gnss_packages_ubuntu_26_plus=(
    build-essential
    cmake
    git
    gnuradio-dev
    gr-limesdr
    gr-osmosdr
    libabsl-dev
    libad9361-dev
    libarmadillo-dev
    libblas-dev
    libboost-chrono-dev
    libboost-date-time-dev
    libboost-dev
    libboost-filesystem-dev
    libboost-serialization-dev
    libboost-thread-dev
    libcpu-features-dev
    libgtest-dev
    libiio-dev
    liblapack-dev
    libmatio-dev
    libpcap-dev
    libprotobuf-dev
    libpugixml-dev
    libssl-dev
    libuhd-dev
    pkgconf
    protobuf-compiler
    python3-mako
  )

  local gnss_packages_older_ubuntu=(
    build-essential
    cmake
    git
    gnuradio-dev
    gr-limesdr
    gr-osmosdr
    libad9361-dev
    libarmadillo-dev
    libblas-dev
    libboost-chrono-dev
    libboost-date-time-dev
    libboost-dev
    libboost-filesystem-dev
    libboost-serialization-dev
    libboost-system-dev
    libboost-thread-dev
    libcpu-features-dev
    libgflags-dev
    libgoogle-glog-dev
    libgtest-dev
    libiio-dev
    liblapack-dev
    liblog4cpp5-dev
    libmatio-dev
    libpcap-dev
    libprotobuf-dev
    libpugixml-dev
    libssl-dev
    libuhd-dev
    pkg-config
    protobuf-compiler
    python3-mako
  )

  if [[ "${os_id}" == "ubuntu" ]] && [[ "${version_id}" != "unknown" ]] && version_ge "${version_id}" "26.04"; then
    echo "[setup] Ubuntu ${version_id} detected; using vendored GNSS-SDR Ubuntu 26.04+ package set." >&2
    GNSS_PACKAGES=("${gnss_packages_ubuntu_26_plus[@]}")
  else
    echo "[setup] ${os_id} ${version_id} detected; using vendored GNSS-SDR older Ubuntu/Debian package set." >&2
    GNSS_PACKAGES=("${gnss_packages_older_ubuntu[@]}")
  fi
}

check_apt_packages_available() {
  local missing=()
  local package

  for package in "$@"; do
    if ! apt-cache show "${package}" >/dev/null 2>&1; then
      missing+=("${package}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "The selected Ubuntu package set has unavailable packages:" >&2
    printf '  %s\n' "${missing[@]}" >&2
    echo "Check /etc/os-release and the vendored gnss-sdr/README.md package list." >&2
    exit 1
  fi
}

package_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q " ok installed"
}

ensure_vendored_gnss_sdr() {
  if [[ ! -f "${GNSS_SRC_DIR}/CMakeLists.txt" ]]; then
    echo "Expected vendored GNSS-SDR source tree at ${GNSS_SRC_DIR}" >&2
    echo "This setup uses the GNSS-SDR tree committed with this repo; it does not clone upstream." >&2
    exit 1
  fi
}

install_system_dependencies() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "apt-get not found; install the GNSS-SDR README dependencies manually." >&2
    return
  fi

  local gnss_packages=()
  select_gnss_packages
  gnss_packages=("${GNSS_PACKAGES[@]}")

  # anti-jamming runtime extras: the backend imports Python UHD directly,
  # and the PyQt GUI/tests import PyQt6. uhd-host supplies UHD image tools
  # and device probes for the fixed X300/HG Port-1 10GbE profile.
  local runtime_packages=(
    ethtool
    iproute2
    iputils-ping
    python3-pip
    python3-venv
    python3-uhd
    python3-pyqt6
    uhd-host
  )
  local all_packages=("${gnss_packages[@]}" "${runtime_packages[@]}")
  local missing_packages=()
  local package

  for package in "${all_packages[@]}"; do
    if ! package_installed "${package}"; then
      missing_packages+=("${package}")
    fi
  done
  if (( ${#missing_packages[@]} == 0 )); then
    echo "[setup] System packages already installed; skipping apt install."
    return
  fi

  run_privileged apt-get update
  check_apt_packages_available "${missing_packages[@]}"
  run_privileged apt-get install -y "${missing_packages[@]}"
}

setup_python_environment() {
  if [[ ! -d "${VENV_DIR}" ]]; then
    "${PYTHON_BIN}" -m venv --system-site-packages "${VENV_DIR}"
  elif [[ -f "${VENV_DIR}/pyvenv.cfg" ]] && grep -q "include-system-site-packages = false" "${VENV_DIR}/pyvenv.cfg"; then
    sed -i "s/include-system-site-packages = false/include-system-site-packages = true/" "${VENV_DIR}/pyvenv.cfg"
  fi

  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
  "${VENV_DIR}/bin/python" -m pip install -r requirements.txt
}

ensure_uhd_images() {
  local hg_image="${UHD_IMAGE_DIR}/usrp_x300_fpga_HG.bit"

  if [[ -f "${hg_image}" ]]; then
    return 0
  fi

  if ! command -v uhd_images_downloader >/dev/null 2>&1; then
    echo "uhd_images_downloader not found; uhd-host did not install correctly." >&2
    exit 1
  fi

  echo "[setup] Downloading X300/HG UHD FPGA image into ${UHD_IMAGE_DIR}."
  run_privileged mkdir -p "${UHD_IMAGE_DIR}"
  run_privileged uhd_images_downloader --types 'x3.*' --install-location "${UHD_IMAGE_DIR}" --yes
}

host_cidr_for_usrp_addr() {
  local addr="$1"
  local a b c d

  IFS=. read -r a b c d <<<"${addr}"
  if [[ -z "${a}" || -z "${b}" || -z "${c}" || -z "${d}" ]]; then
    echo "Cannot derive host CIDR from USRP address ${addr}" >&2
    exit 1
  fi
  echo "${a}.${b}.${c}.1/24"
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

ensure_wired_carrier_up() {
  local iface="$1"
  local link_line
  local carrier="unknown"
  local operstate="unknown"

  if [[ -r "/sys/class/net/${iface}/carrier" ]]; then
    carrier="$(cat "/sys/class/net/${iface}/carrier" 2>/dev/null || echo unknown)"
  fi
  if [[ -r "/sys/class/net/${iface}/operstate" ]]; then
    operstate="$(cat "/sys/class/net/${iface}/operstate" 2>/dev/null || echo unknown)"
  fi
  link_line="$(ip -o link show dev "${iface}" 2>/dev/null || true)"

  if [[ "${carrier}" != "1" ]] || ! grep -Fq "LOWER_UP" <<<"${link_line}"; then
    echo "USRP Ethernet interface ${iface} has no physical carrier." >&2
    echo "Observed link: ${link_line:-missing}" >&2
    echo "Observed carrier=${carrier}, operstate=${operstate}." >&2
    echo "Check the Ethernet cable, X300 power, and that the cable is on the selected X300 port." >&2
    exit 1
  fi
  echo "[setup] ${iface} physical link is up: carrier=${carrier}, operstate=${operstate}, flags=$(sed -n 's/^[^<]*<\([^>]*\)>.*/\1/p' <<<"${link_line}")." >&2
}

is_physical_ethernet_iface() {
  local iface="$1"
  local net_path="/sys/class/net/${iface}"

  [[ -e "${net_path}" ]] || return 1
  [[ -e "${net_path}/device" ]] || return 1
  [[ -r "${net_path}/type" ]] && [[ "$(cat "${net_path}/type")" == "1" ]] || return 1
  [[ ! -d "${net_path}/wireless" ]] || return 1
}

ensure_usrp_route_on_iface() {
  local addr="$1"
  local iface="$2"
  local routed_iface
  local route_line

  route_line="$(ip route get "${addr}" 2>/dev/null | head -n1 || true)"
  routed_iface="$(route_iface_for_ip "${addr}")"
  if [[ "${routed_iface}" != "${iface}" ]]; then
    echo "Route to USRP ${addr} does not use ${iface}." >&2
    echo "Observed route: ${route_line:-missing}" >&2
    echo "This usually means Linux would send USRP traffic over Wi-Fi/default route instead of Ethernet." >&2
    echo "Expected host CIDR is ${USRP_HOST_CIDR} on ${iface}." >&2
    exit 1
  fi
  echo "[setup] Route to USRP ${addr} uses ${iface}: ${route_line}."
}

detect_usrp_iface() {
  local iface
  local speed
  local best_iface=""
  local best_speed="-1"
  local net_path

  if [[ -n "${USRP_IFACE}" ]]; then
    if ! ip link show dev "${USRP_IFACE}" >/dev/null 2>&1; then
      echo "Configured USRP host interface ${USRP_IFACE} was not found." >&2
      exit 1
    fi
    if ! is_physical_ethernet_iface "${USRP_IFACE}"; then
      echo "Configured USRP host interface ${USRP_IFACE} is not a physical Ethernet interface." >&2
      exit 1
    fi
    run_privileged ip link set dev "${USRP_IFACE}" up
    ensure_wired_carrier_up "${USRP_IFACE}"
    echo "${USRP_IFACE}"
    return 0
  fi

  for net_path in /sys/class/net/*; do
    iface="${net_path##*/}"
    is_physical_ethernet_iface "${iface}" || continue
    run_privileged ip link set dev "${iface}" up
    sleep 0.2
    [[ -r "${net_path}/carrier" ]] && [[ "$(cat "${net_path}/carrier")" == "1" ]] || continue
    speed="0"
    if [[ -r "${net_path}/speed" ]]; then
      speed="$(cat "${net_path}/speed" 2>/dev/null || echo 0)"
    fi
    [[ "${speed}" =~ ^[0-9]+$ ]] || speed="0"
    if (( speed > best_speed )); then
      best_speed="${speed}"
      best_iface="${iface}"
    fi
  done

  if [[ -n "${best_iface}" ]]; then
    ensure_wired_carrier_up "${best_iface}"
    echo "${best_iface}"
    return 0
  fi

  echo "Could not auto-detect a live wired USRP interface." >&2
  echo "Set ANTIJAMMING_USRP_IFACE to the Ethernet interface connected to the X300." >&2
  exit 1
}

configure_x300_host_link_current_boot() {
  local iface="$1"
  local host_cidr="$2"

  run_privileged ip link set dev "${iface}" up
  run_privileged ip link set dev "${iface}" mtu "${USRP_MTU}"
  ensure_wired_carrier_up "${iface}"
  if ! ip -4 addr show dev "${iface}" | grep -Fq "${host_cidr}"; then
    run_privileged ip -4 addr flush dev "${iface}" scope global
    run_privileged ip addr add "${host_cidr}" dev "${iface}"
  fi
}

find_x300_at_addr() {
  local addr="$1"
  local probe_text

  probe_text="$(timeout 8 uhd_find_devices --args "addr=${addr}" 2>&1 || true)"
  grep -Eiq '^[[:space:]]*(product:[[:space:]]*X300|type:[[:space:]]*x300)[[:space:]]*$' <<<"${probe_text}"
}

resolve_x300_host_link() {
  local candidate_addr
  local candidate_host_cidr

  if ! command -v uhd_find_devices >/dev/null 2>&1; then
    echo "uhd_find_devices not found; uhd-host did not install correctly." >&2
    exit 1
  fi

  USRP_IFACE="$(detect_usrp_iface)"
  if [[ -n "${USRP_ADDR}" ]]; then
    USRP_HOST_CIDR="${USRP_HOST_CIDR:-$(host_cidr_for_usrp_addr "${USRP_ADDR}")}"
    echo "[setup] Using configured USRP path: ${USRP_IFACE} -> ${USRP_ADDR} (${USRP_HOST_CIDR})."
    return 0
  fi

  # Probe an already-configured candidate before changing any host address. This
  # preserves a live X300/GNSS-SDR link when setup is rerun while the radio is on.
  for candidate_addr in ${USRP_ADDR_CANDIDATES}; do
    candidate_host_cidr="$(host_cidr_for_usrp_addr "${candidate_addr}")"
    if ! ip -4 addr show dev "${USRP_IFACE}" | grep -Fq "${candidate_host_cidr}"; then
      continue
    fi
    echo "[setup] Probing currently configured X300 path ${USRP_IFACE} -> ${candidate_addr}."
    ensure_usrp_route_on_iface "${candidate_addr}" "${USRP_IFACE}"
    if find_x300_at_addr "${candidate_addr}"; then
      USRP_ADDR="${candidate_addr}"
      USRP_HOST_CIDR="${candidate_host_cidr}"
      echo "[setup] Found X300 at ${USRP_ADDR} without reconfiguring the live host link."
      return 0
    fi
  done

  echo "[setup] Auto-detecting X300 on ${USRP_IFACE}; candidates: ${USRP_ADDR_CANDIDATES}."
  for candidate_addr in ${USRP_ADDR_CANDIDATES}; do
    candidate_host_cidr="$(host_cidr_for_usrp_addr "${candidate_addr}")"
    echo "[setup] Probing X300 candidate ${candidate_addr} via ${candidate_host_cidr} on ${USRP_IFACE}."
    configure_x300_host_link_current_boot "${USRP_IFACE}" "${candidate_host_cidr}"
    ensure_usrp_route_on_iface "${candidate_addr}" "${USRP_IFACE}"
    if find_x300_at_addr "${candidate_addr}"; then
      USRP_ADDR="${candidate_addr}"
      USRP_HOST_CIDR="${candidate_host_cidr}"
      echo "[setup] Found X300 at ${USRP_ADDR} on ${USRP_IFACE}."
      return 0
    fi
  done

  echo "No X300 was found on ${USRP_IFACE} at candidates: ${USRP_ADDR_CANDIDATES}" >&2
  echo "Set ANTIJAMMING_USRP_ADDR and ANTIJAMMING_USRP_IFACE if this host uses a custom address or port." >&2
  exit 1
}

persist_runtime_usrp_addr() {
  if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
    echo "Runtime configuration not found at ${RUNTIME_CONFIG}" >&2
    exit 1
  fi
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Python environment not found at ${VENV_DIR}" >&2
    exit 1
  fi

  ANTIJAMMING_DETECTED_USRP_ADDR="${USRP_ADDR}" \
  ANTIJAMMING_RUNTIME_CONFIG="${RUNTIME_CONFIG}" \
  "${VENV_DIR}/bin/python" - <<'PY'
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile

path = Path(os.environ["ANTIJAMMING_RUNTIME_CONFIG"])
address = str(
    ipaddress.IPv4Address(os.environ["ANTIJAMMING_DETECTED_USRP_ADDR"])
)
source = path.read_text(encoding="utf-8")
payload = json.loads(source)
previous = str(payload.get("usrp_addr", "") or "")
replacement = f"addr={address}"
if re.search(r"(?:^|,)addr=[^,]*", previous):
    updated = re.sub(
        r"(^|,)addr=[^,]*",
        lambda match: f"{match.group(1)}{replacement}",
        previous,
        count=1,
    )
else:
    updated = replacement + (f",{previous}" if previous else "")

if updated == previous:
    print(f"[setup] Runtime USRP address already configured: {updated}")
    raise SystemExit(0)

value_pattern = re.compile(
    r'(?m)^(\s*"usrp_addr"\s*:\s*)("(?:\\.|[^"\\])*")'
)
matches = list(value_pattern.finditer(source))
if len(matches) != 1:
    raise SystemExit(
        f"Expected exactly one usrp_addr string in {path}; found {len(matches)}"
    )
value_match = matches[0]
updated_source = (
    source[: value_match.start(2)]
    + json.dumps(updated)
    + source[value_match.end(2) :]
)
mode = stat.S_IMODE(path.stat().st_mode)
temporary_name = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(updated_source)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_name = temporary.name
    os.chmod(temporary_name, mode)
    os.replace(temporary_name, path)
finally:
    if temporary_name and os.path.exists(temporary_name):
        os.unlink(temporary_name)

print(f"[setup] Updated runtime USRP address: {previous or '(unset)'} -> {updated}")
PY
}

ensure_x300_host_link() {
  if ! ip link show dev "${USRP_IFACE}" >/dev/null 2>&1; then
    echo "Configured USRP host interface ${USRP_IFACE} was not found." >&2
    echo "Set ANTIJAMMING_USRP_IFACE to the 10GbE SFP+ interface name." >&2
    exit 1
  fi

  echo "[setup] Configuring USRP 10GbE host link on ${USRP_IFACE}."
  configure_x300_host_link_current_boot "${USRP_IFACE}" "${USRP_HOST_CIDR}"
  ensure_usrp_route_on_iface "${USRP_ADDR}" "${USRP_IFACE}"
}

ensure_x300_network_profile() {
  if ! command -v nmcli >/dev/null 2>&1; then
    echo "[setup] nmcli not found; applying only the current-boot USRP link state."
    return
  fi

  echo "[setup] Configuring persistent NetworkManager profile ${USRP_NM_PROFILE} on ${USRP_IFACE}."
  local profile_uuid=""
  local active_profile_uuid=""
  local iface_profile_uuid=""
  local add_output=""
  local connection_name=""
  local connection_uuid=""
  local connection_type=""
  local connection_device=""
  local connection_iface=""
  local duplicate_uuids=()
  local competing_uuids=()

  while IFS=: read -r connection_name connection_uuid connection_type connection_device; do
    if [[ "${connection_name}" == "${USRP_NM_PROFILE}" ]] && [[ "${connection_type}" == "802-3-ethernet" ]]; then
      duplicate_uuids+=("${connection_uuid}")
      if [[ "${connection_device}" == "${USRP_IFACE}" ]] && [[ -z "${active_profile_uuid}" ]]; then
        active_profile_uuid="${connection_uuid}"
      elif [[ -z "${profile_uuid}" ]]; then
        profile_uuid="${connection_uuid}"
      fi
    elif [[ "${connection_name}" == "${USRP_IFACE}" ]] && [[ "${connection_type}" == "802-3-ethernet" ]] && [[ -z "${iface_profile_uuid}" ]]; then
      iface_profile_uuid="${connection_uuid}"
    fi
  done < <(nmcli -t -f NAME,UUID,TYPE,DEVICE connection show)

  if [[ -n "${active_profile_uuid}" ]]; then
    profile_uuid="${active_profile_uuid}"
  elif [[ -z "${profile_uuid}" ]] && [[ -n "${iface_profile_uuid}" ]]; then
    profile_uuid="${iface_profile_uuid}"
  fi

  if [[ -z "${profile_uuid}" ]]; then
    add_output="$(
      run_privileged nmcli connection add type ethernet \
        ifname "${USRP_IFACE}" \
        con-name "${USRP_NM_PROFILE}"
    )"
    profile_uuid="$(sed -n 's/.*(\([0-9a-fA-F-]\{36\}\)).*/\1/p' <<<"${add_output}" | head -n1)"
    if [[ -z "${profile_uuid}" ]]; then
      echo "Could not determine UUID of created NetworkManager profile ${USRP_NM_PROFILE}." >&2
      echo "${add_output}" >&2
      exit 1
    fi
  else
    run_privileged nmcli connection modify uuid "${profile_uuid}" \
      connection.id "${USRP_NM_PROFILE}" \
      connection.interface-name "${USRP_IFACE}"
  fi

  for connection_uuid in "${duplicate_uuids[@]}"; do
    if [[ "${connection_uuid}" != "${profile_uuid}" ]]; then
      echo "[setup] Removing duplicate NetworkManager profile ${USRP_NM_PROFILE} (${connection_uuid})."
      run_privileged nmcli connection delete uuid "${connection_uuid}" >/dev/null
    fi
  done

  while IFS=: read -r connection_uuid connection_type; do
    [[ "${connection_type}" == "802-3-ethernet" ]] || continue
    [[ "${connection_uuid}" != "${profile_uuid}" ]] || continue
    connection_iface="$(nmcli -g connection.interface-name connection show uuid "${connection_uuid}" 2>/dev/null || true)"
    if [[ "${connection_iface}" == "${USRP_IFACE}" ]]; then
      competing_uuids+=("${connection_uuid}")
    fi
  done < <(nmcli -t -f UUID,TYPE connection show)

  for connection_uuid in "${competing_uuids[@]}"; do
    echo "[setup] Disabling competing NetworkManager profile ${connection_uuid} on ${USRP_IFACE}."
    run_privileged nmcli connection modify uuid "${connection_uuid}" connection.autoconnect no
  done

  run_privileged nmcli connection modify uuid "${profile_uuid}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    ipv4.method manual \
    ipv4.addresses "${USRP_HOST_CIDR}" \
    ipv4.never-default yes \
    ipv6.method ignore \
    802-3-ethernet.mtu "${USRP_MTU}"

  if ! is_physical_ethernet_iface "${USRP_IFACE}"; then
    echo "Refusing to activate USRP profile on non-Ethernet interface ${USRP_IFACE}." >&2
    exit 1
  fi
  if [[ "${active_profile_uuid}" == "${profile_uuid}" ]] \
      && ip -4 addr show dev "${USRP_IFACE}" | grep -Fq "${USRP_HOST_CIDR}"; then
    echo "[setup] Preserving active USRP NetworkManager connection ${profile_uuid}."
  else
    run_privileged nmcli connection up uuid "${profile_uuid}" >/dev/null
  fi
  ensure_wired_carrier_up "${USRP_IFACE}"
  ensure_usrp_route_on_iface "${USRP_ADDR}" "${USRP_IFACE}"
}

ensure_x300_host_socket_buffers() {
  echo "[setup] Configuring host socket buffers for UHD streaming."
  run_privileged sysctl -w "net.core.rmem_max=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
  run_privileged sysctl -w "net.core.rmem_default=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
  run_privileged sysctl -w "net.core.wmem_max=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
  run_privileged sysctl -w "net.core.wmem_default=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
}

ensure_x300_hg_image_loaded() {
  local hg_image="${UHD_IMAGE_DIR}/usrp_x300_fpga_HG.bit"
  local probe_text

  if ! command -v uhd_find_devices >/dev/null 2>&1; then
    echo "uhd_find_devices not found; uhd-host did not install correctly." >&2
    exit 1
  fi
  if ! command -v uhd_image_loader >/dev/null 2>&1; then
    echo "uhd_image_loader not found; UHD FPGA image management is unavailable." >&2
    exit 1
  fi
  if [[ ! -f "${hg_image}" ]]; then
    echo "X300 HG FPGA image not found at ${hg_image}" >&2
    echo "Run setup again after uhd_images_downloader installs UHD images." >&2
    exit 1
  fi

  probe_text="$(uhd_find_devices --args "addr=${USRP_ADDR}" 2>&1 || true)"
  if grep -Eiq '^[[:space:]]*fpga:[[:space:]]*HG[[:space:]]*$' <<<"${probe_text}"; then
    echo "[setup] USRP ${USRP_ADDR} already reports FPGA image HG."
    return 0
  fi

  if grep -Eiq '^[[:space:]]*fpga:' <<<"${probe_text}"; then
    echo "[setup] Loading HG FPGA image onto USRP ${USRP_ADDR}."
    uhd_image_loader --args "type=x300,addr=${USRP_ADDR}" --fpga-path "${hg_image}"
    echo "[setup] HG FPGA image loader finished. Power-cycle the USRP if UHD requests it."
    return 0
  fi

  echo "[setup] USRP ${USRP_ADDR} did not report an FPGA image through fixed-address UHD probe." >&2
  echo "[setup] Confirm cabling/IP, then run setup again before launching the GUI." >&2
  exit 1
}

build_local_gnss_sdr() {
  cmake -S "${GNSS_SRC_DIR}" \
    -B "${GNSS_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${GNSS_INSTALL_DIR}" \
    -DENABLE_UHD=ON \
    -DENABLE_OSMOSDR=OFF \
    -DENABLE_LIMESDR=OFF \
    -DENABLE_UNIT_TESTING=OFF \
    -DENABLE_UNIT_TESTING_MINIMAL=OFF \
    -DENABLE_UNIT_TESTING_EXTRA=OFF \
    -DENABLE_SYSTEM_TESTING=OFF \
    -DENABLE_SYSTEM_TESTING_EXTRA=OFF \
    -DENABLE_INSTALL_TESTS=OFF \
    -DENABLE_GNSS_SIM_INSTALL=OFF

  cmake --build "${GNSS_BUILD_DIR}" -j"$(nproc)"
}

profile_local_gnss_sdr() {
  local volk_args=()
  local gnss_volk_args=()

  if ! command -v volk_profile >/dev/null 2>&1; then
    echo "volk_profile not found; install libvolk-bin or the package that provides GNU Radio VOLK profiling." >&2
    exit 1
  fi
  if [[ ! -x "${GNSS_VOLK_PROFILE_BIN}" ]]; then
    echo "Repo-local volk_gnsssdr_profile not found at ${GNSS_VOLK_PROFILE_BIN}" >&2
    exit 1
  fi

  if [[ -f "${VOLK_CONFIG_FILE}" ]]; then
    volk_args=(--update)
  fi
  if [[ -f "${GNSS_VOLK_CONFIG_FILE}" ]]; then
    gnss_volk_args=(--update)
  fi

  echo "[setup] Profiling GNU Radio VOLK kernels for this CPU."
  volk_profile "${volk_args[@]}"
  echo "[setup] Profiling repo-local VOLK_GNSSSDR kernels for this CPU."
  "${GNSS_VOLK_PROFILE_BIN}" "${gnss_volk_args[@]}"
}

verify_setup() {
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Python environment was not created at ${VENV_DIR}" >&2
    exit 1
  fi

  "${VENV_DIR}/bin/python" - <<'PY'
modules = ("numpy", "scipy", "h5py", "pyqtgraph", "pytest", "pytestqt", "serial", "PyQt6", "uhd")
missing = []
for module in modules:
    try:
        __import__(module)
    except Exception as exc:
        missing.append(f"{module}: {type(exc).__name__}: {exc}")
if missing:
    raise SystemExit("Missing Python runtime modules:\n  " + "\n  ".join(missing))
PY

  for tool_name in ip ping ethtool; do
    if ! command -v "${tool_name}" >/dev/null 2>&1; then
      echo "${tool_name} not found; fixed X300 10GbE host-link checks are unavailable." >&2
      exit 1
    fi
  done

  if ! command -v uhd_usrp_probe >/dev/null 2>&1; then
    echo "uhd_usrp_probe not found; uhd-host did not install correctly." >&2
    exit 1
  fi

  if ! command -v uhd_image_loader >/dev/null 2>&1; then
    echo "uhd_image_loader not found; UHD FPGA image management is unavailable." >&2
    exit 1
  fi

  if [[ ! -f "${UHD_IMAGE_DIR}/usrp_x300_fpga_HG.bit" ]]; then
    echo "X300 HG FPGA image not found at ${UHD_IMAGE_DIR}/usrp_x300_fpga_HG.bit" >&2
    echo "Run uhd_images_downloader after installing uhd-host." >&2
    exit 1
  fi

  if [[ ! -x "${GNSS_BUILD_BIN}" ]]; then
    echo "Repo-local GNSS-SDR build binary not found at ${GNSS_BUILD_BIN}" >&2
    exit 1
  fi

  "${GNSS_BUILD_BIN}" --version >/dev/null

  if [[ ! -f "${VOLK_CONFIG_FILE}" ]]; then
    echo "VOLK profile config was not created at ${VOLK_CONFIG_FILE}" >&2
    exit 1
  fi

  if [[ ! -f "${GNSS_VOLK_CONFIG_FILE}" ]]; then
    echo "VOLK_GNSSSDR profile config was not created at ${GNSS_VOLK_CONFIG_FILE}" >&2
    exit 1
  fi

  if [[ ! -f "${PHASE_CALIBRATION_FILE}" ]]; then
    echo "Repo-local phase calibration file not found at ${PHASE_CALIBRATION_FILE}" >&2
    exit 1
  fi
}

ensure_vendored_gnss_sdr
install_system_dependencies
setup_python_environment
ensure_uhd_images
resolve_x300_host_link
persist_runtime_usrp_addr
ensure_x300_host_link
ensure_x300_network_profile
ensure_x300_host_socket_buffers
ensure_x300_hg_image_loaded
build_local_gnss_sdr
profile_local_gnss_sdr
verify_setup

echo
echo "antijamming setup is ready."
echo "Python: ${VENV_DIR}/bin/python"
echo "Repo-local GNSS-SDR source: ${GNSS_SRC_DIR}"
echo "Repo-local GNSS-SDR build: ${GNSS_BUILD_BIN}"
echo "Repo-local phase calibration: ${PHASE_CALIBRATION_FILE}"
echo "USRP HG Port-1 address: ${USRP_ADDR}"
echo "USRP host link: ${USRP_IFACE} ${USRP_HOST_CIDR} mtu ${USRP_MTU}"
echo "USRP NetworkManager profile: ${USRP_NM_PROFILE}"
echo "USRP socket buffers: ${USRP_SOCKET_BUFFER_BYTES}"
echo "GNSS-SDR binary candidates:"
echo "  ${GNSS_BUILD_BIN}"
echo "  ${GNSS_INSTALL_DIR}/gnss-sdr"
echo "  ${GNSS_INSTALL_DIR}/bin/gnss-sdr"
