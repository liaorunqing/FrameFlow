from pathlib import Path

from backend.app.integrations import integration_capabilities, shotcraft_summary


def test_integration_registry_has_all_requested_modules():
    identifiers = {item.id for item in integration_capabilities()}
    assert {"video-shotcraft", "pyscenedetect", "dover-mobile", "vbench", "remotion", "audio-mastering"} <= identifiers


def test_shotcraft_catalog_is_readable():
    summary = shotcraft_summary()
    assert summary["available"] is True
    assert summary["cards"] >= 100
    assert summary["styles"] >= 150
