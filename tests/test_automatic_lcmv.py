"""Automatic arming contracts; no USRP, RF source or receiver process required."""

from dataclasses import fields
import json
import logging

import pytest

from antijamming import jsonc
from antijamming.config import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    StreamConfig,
    load_stream_config_file,
)
from antijamming.runtime import BackendRuntime
from antijamming.runtime.remote_worker import RemoteStreamWorker


@pytest.mark.parametrize(
    "name",
    ["lcmv_test_enabled", "lcmv_auto_arm_after_pvt", "one_run_segmentation_enabled"],
)
def test_manual_arming_switches_are_not_product_configuration(name, tmp_path):
    profile = jsonc.load(DEFAULT_RUNTIME_CONFIG_PATH)
    assert name not in profile
    assert name not in {field.name for field in fields(StreamConfig)}
    with pytest.raises(TypeError, match="Unexpected StreamConfig override"):
        StreamConfig(**{name: True})
    profile[name] = True
    path = tmp_path / "obsolete.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown"):
        load_stream_config_file(path)


def test_runtime_and_remote_worker_do_not_expose_manual_lcmv_setter():
    assert not hasattr(BackendRuntime, "set_lcmv_test_enabled")
    assert not hasattr(RemoteStreamWorker, "set_lcmv_test_enabled")


def test_manifest_reports_automatic_policy_not_a_configured_enable_switch():
    runtime = BackendRuntime(
        StreamConfig(),
        {name: logging.getLogger(f"test.automatic.{name}") for name in (
            "app", "errors", "lcmv", "gnss", "stream", "handoff", "hw",
            "transport", "phase", "doa", "analysis", "lcmv_pattern",
            "spatial_vector", "health",
        )},
    )
    manifest = runtime._lcmv_runtime_manifest()
    assert manifest["lcmv_arming_policy"] == "automatic_after_healthy_pvt"
    assert manifest["lcmv_armed"] is False
    assert "lcmv_test_enabled" not in manifest
    assert "lcmv_auto_arm_after_pvt" not in manifest
