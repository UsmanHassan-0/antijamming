#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GNSS_SRC_DIR="${ROOT_DIR}/gnss-sdr"
GNSS_BUILD_DIR="${GNSS_SRC_DIR}/build-antijamming"
GNSS_INSTALL_DIR="${GNSS_SRC_DIR}/install"
GNSS_BUILD_BIN="${GNSS_BUILD_DIR}/src/main/gnss-sdr"
PHASE_CALIBRATION_FILE="${ROOT_DIR}/configs/calibration/x300_phase_offsets_100khz.json"
UHD_IMAGE_DIR="${UHD_IMAGE_DIR:-/usr/share/uhd/images}"
USRP_ADDR="${ANTIJAMMING_USRP_ADDR:-192.168.40.2}"
USRP_IFACE="${ANTIJAMMING_USRP_IFACE:-enp6s0f1np1}"
USRP_NM_PROFILE="${ANTIJAMMING_USRP_NM_PROFILE:-usrp-x300}"
USRP_HOST_CIDR="${ANTIJAMMING_USRP_HOST_CIDR:-192.168.40.1/24}"
USRP_MTU="${ANTIJAMMING_USRP_MTU:-9000}"
USRP_SOCKET_BUFFER_BYTES="${ANTIJAMMING_USRP_SOCKET_BUFFER_BYTES:-50000000}"
VENV_DIR="${ROOT_DIR}/.aj"
PYTHON_BIN="${PYTHON_BIN:-python3}"

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
  # and device probes for the fixed X300/XG 10GbE profile.
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
  local xg_image="${UHD_IMAGE_DIR}/usrp_x300_fpga_XG.bit"

  if [[ -f "${xg_image}" ]]; then
    return 0
  fi

  if ! command -v uhd_images_downloader >/dev/null 2>&1; then
    echo "uhd_images_downloader not found; uhd-host did not install correctly." >&2
    exit 1
  fi

  echo "[setup] Downloading X300/XG UHD FPGA image into ${UHD_IMAGE_DIR}."
  run_privileged mkdir -p "${UHD_IMAGE_DIR}"
  run_privileged uhd_images_downloader --types 'x3.*' --install-location "${UHD_IMAGE_DIR}" --yes
}

ensure_x300_host_link() {
  if ! ip link show dev "${USRP_IFACE}" >/dev/null 2>&1; then
    echo "Configured USRP host interface ${USRP_IFACE} was not found." >&2
    echo "Set ANTIJAMMING_USRP_IFACE to the 10GbE SFP+ interface name." >&2
    exit 1
  fi

  echo "[setup] Configuring USRP 10GbE host link on ${USRP_IFACE}."
  run_privileged ip link set dev "${USRP_IFACE}" up
  run_privileged ip link set dev "${USRP_IFACE}" mtu "${USRP_MTU}"
  if ! ip -4 addr show dev "${USRP_IFACE}" | grep -Fq "${USRP_HOST_CIDR}"; then
    run_privileged ip -4 addr flush dev "${USRP_IFACE}" scope global
    run_privileged ip addr add "${USRP_HOST_CIDR}" dev "${USRP_IFACE}"
  fi
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
  local duplicate_uuids=()

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

  run_privileged nmcli connection modify uuid "${profile_uuid}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    ipv4.method manual \
    ipv4.addresses "${USRP_HOST_CIDR}" \
    ipv4.never-default yes \
    ipv6.method ignore \
    802-3-ethernet.mtu "${USRP_MTU}"

  if nmcli -t -f DEVICE device status | grep -Fq "${USRP_IFACE}:"; then
    run_privileged nmcli connection up uuid "${profile_uuid}" >/dev/null || true
  fi
}

ensure_x300_host_socket_buffers() {
  echo "[setup] Configuring host socket buffers for UHD streaming."
  run_privileged sysctl -w "net.core.rmem_max=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
  run_privileged sysctl -w "net.core.rmem_default=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
  run_privileged sysctl -w "net.core.wmem_max=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
  run_privileged sysctl -w "net.core.wmem_default=${USRP_SOCKET_BUFFER_BYTES}" >/dev/null
}

ensure_x300_xg_image_loaded() {
  local xg_image="${UHD_IMAGE_DIR}/usrp_x300_fpga_XG.bit"
  local probe_text

  if ! command -v uhd_find_devices >/dev/null 2>&1; then
    echo "uhd_find_devices not found; uhd-host did not install correctly." >&2
    exit 1
  fi
  if ! command -v uhd_image_loader >/dev/null 2>&1; then
    echo "uhd_image_loader not found; UHD FPGA image management is unavailable." >&2
    exit 1
  fi
  if [[ ! -f "${xg_image}" ]]; then
    echo "X300 XG FPGA image not found at ${xg_image}" >&2
    echo "Run setup again after uhd_images_downloader installs UHD images." >&2
    exit 1
  fi

  probe_text="$(uhd_find_devices --args "addr=${USRP_ADDR}" 2>&1 || true)"
  if grep -Eiq '^[[:space:]]*fpga:[[:space:]]*XG[[:space:]]*$' <<<"${probe_text}"; then
    echo "[setup] USRP ${USRP_ADDR} already reports FPGA image XG."
    return 0
  fi

  if grep -Eiq '^[[:space:]]*fpga:' <<<"${probe_text}"; then
    echo "[setup] Loading XG FPGA image onto USRP ${USRP_ADDR}."
    uhd_image_loader --args "type=x300,addr=${USRP_ADDR}" --fpga-path "${xg_image}"
    echo "[setup] XG FPGA image loader finished. Power-cycle the USRP if UHD requests it."
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

  if [[ ! -f "${UHD_IMAGE_DIR}/usrp_x300_fpga_XG.bit" ]]; then
    echo "X300 XG FPGA image not found at ${UHD_IMAGE_DIR}/usrp_x300_fpga_XG.bit" >&2
    echo "Run uhd_images_downloader after installing uhd-host." >&2
    exit 1
  fi

  if [[ ! -x "${GNSS_BUILD_BIN}" ]]; then
    echo "Repo-local GNSS-SDR build binary not found at ${GNSS_BUILD_BIN}" >&2
    exit 1
  fi

  "${GNSS_BUILD_BIN}" --version >/dev/null

  if [[ ! -f "${PHASE_CALIBRATION_FILE}" ]]; then
    echo "Repo-local phase calibration file not found at ${PHASE_CALIBRATION_FILE}" >&2
    exit 1
  fi
}

ensure_vendored_gnss_sdr
install_system_dependencies
setup_python_environment
ensure_uhd_images
ensure_x300_host_link
ensure_x300_network_profile
ensure_x300_host_socket_buffers
ensure_x300_xg_image_loaded
build_local_gnss_sdr
verify_setup

echo
echo "antijamming setup is ready."
echo "Python: ${VENV_DIR}/bin/python"
echo "Repo-local GNSS-SDR source: ${GNSS_SRC_DIR}"
echo "Repo-local GNSS-SDR build: ${GNSS_BUILD_BIN}"
echo "Repo-local phase calibration: ${PHASE_CALIBRATION_FILE}"
echo "USRP XG address: ${USRP_ADDR}"
echo "USRP host link: ${USRP_IFACE} ${USRP_HOST_CIDR} mtu ${USRP_MTU}"
echo "USRP NetworkManager profile: ${USRP_NM_PROFILE}"
echo "USRP socket buffers: ${USRP_SOCKET_BUFFER_BYTES}"
echo "GNSS-SDR binary candidates:"
echo "  ${GNSS_BUILD_BIN}"
echo "  ${GNSS_INSTALL_DIR}/gnss-sdr"
echo "  ${GNSS_INSTALL_DIR}/bin/gnss-sdr"
