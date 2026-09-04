#!/usr/bin/env bash

# Shared UHD runtime selection for anti-jamming entrypoints. This file is
# sourced; it deliberately does not modify the caller's environment until the
# activation function succeeds.

ANTIJAMMING_UHD_REQUIRED_VERSION="4.10.0"
ANTIJAMMING_UHD_DEFAULT_PREFIX="${HOME}/uhd-official-4.10.0.0/install-host"

antijamming_activate_uhd_runtime() {
  local python_bin="$1"
  local prefix="${ANTIJAMMING_UHD_PREFIX:-${ANTIJAMMING_UHD_DEFAULT_PREFIX}}"
  local python_version
  local python_site
  local version_output
  local active_version
  local module_path
  local binding_path

  if ! command -v "${python_bin}" >/dev/null 2>&1; then
    echo "Anti-jamming Python executable not found: ${python_bin}" >&2
    return 1
  fi

  prefix="$(readlink -f -- "${prefix}" 2>/dev/null || true)"
  if [[ -z "${prefix}" || ! -d "${prefix}" ]]; then
    echo "Pinned UHD runtime prefix is missing: ${prefix:-unset}" >&2
    echo "Run tools/build_uhd_runtime.sh before starting anti-jamming." >&2
    return 1
  fi

  python_version="$("${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  python_site="${prefix}/lib/python${python_version}/site-packages"
  if [[ ! -f "${prefix}/lib/libuhd.so.${ANTIJAMMING_UHD_REQUIRED_VERSION}" ]]; then
    echo "Pinned UHD shared library is missing under ${prefix}/lib." >&2
    echo "Expected libuhd.so.${ANTIJAMMING_UHD_REQUIRED_VERSION}." >&2
    return 1
  fi
  if [[ ! -f "${python_site}/uhd/__init__.py" ]]; then
    echo "Pinned UHD Python module is missing under ${python_site}." >&2
    return 1
  fi

  version_output="$(
    env \
      PYTHONPATH="${python_site}${PYTHONPATH:+:${PYTHONPATH}}" \
      LD_LIBRARY_PATH="${prefix}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
      "${python_bin}" - <<'PY'
from pathlib import Path

import uhd

print(uhd.get_version_string())
print(Path(uhd.__file__).resolve())
print(Path(uhd.libpyuhd.__file__).resolve())
PY
  )" || {
    echo "Pinned UHD ${ANTIJAMMING_UHD_REQUIRED_VERSION} Python import failed." >&2
    return 1
  }

  active_version="$(sed -n '1p' <<<"${version_output}")"
  module_path="$(sed -n '2p' <<<"${version_output}")"
  binding_path="$(sed -n '3p' <<<"${version_output}")"
  if [[ "${active_version}" != "${ANTIJAMMING_UHD_REQUIRED_VERSION}"* ]]; then
    echo "Wrong UHD runtime selected: ${active_version:-unknown}." >&2
    echo "Required version prefix: ${ANTIJAMMING_UHD_REQUIRED_VERSION}." >&2
    return 1
  fi
  if [[ "${module_path}" != "${prefix}/"* || "${binding_path}" != "${prefix}/"* ]]; then
    echo "UHD Python import escaped the pinned prefix ${prefix}." >&2
    echo "Module: ${module_path:-unknown}" >&2
    echo "Binding: ${binding_path:-unknown}" >&2
    return 1
  fi

  export ANTIJAMMING_UHD_PREFIX="${prefix}"
  export PYTHONPATH="${python_site}${PYTHONPATH:+:${PYTHONPATH}}"
  export LD_LIBRARY_PATH="${prefix}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
  export PATH="${prefix}/bin:${PATH}"

  echo "[uhd-runtime] version=${active_version}"
  echo "[uhd-runtime] prefix=${prefix}"
  echo "[uhd-runtime] python_binding=${binding_path}"
}
