"""Tests for the device rules in device.py."""

import pytest

from vega_annotator.device import MachineFacts, parse_nvidia_smi, plan_for, unsupported_reason


def mac(ram, cores=4):
    return MachineFacts(system="Darwin", machine="arm64", ram_gb=ram, physical_cores=cores)


def windows(ram=16, vram=0, name=""):
    return MachineFacts(system="Windows", machine="AMD64", ram_gb=ram, physical_cores=8,
                        nvidia_vram_gb=vram, nvidia_name=name)


@pytest.mark.parametrize("ram", [8, 16, 23.9])
def test_small_mac_uses_cpu_float32(ram):
    plan = plan_for(mac(ram))
    assert (plan.tracker, plan.exemplar, plan.tracker_dtype) == ("cpu:0", "cpu:0", "float32")
    assert "processor" in plan.label


@pytest.mark.parametrize("ram", [24, 36, 96])
def test_large_mac_uses_gpu_for_clicks_and_cpu_for_find_similar(ram):
    plan = plan_for(mac(ram, cores=12))
    assert (plan.tracker, plan.exemplar, plan.tracker_dtype) == ("mps:0", "cpu:0", "bfloat16")
    assert plan.threads == 6


def test_find_similar_loads_at_first_use_below_24_gb():
    assert plan_for(mac(8)).warm_exemplar is False
    assert plan_for(mac(16)).warm_exemplar is False
    assert plan_for(mac(24)).warm_exemplar is True


def test_person_can_force_gpu_or_cpu():
    assert plan_for(mac(16), "gpu").tracker == "mps:0"
    assert plan_for(mac(96), "cpu").tracker == "cpu:0"
    assert plan_for(mac(96), "cpu").tracker_dtype == "float32"


def test_bfloat16_is_never_planned_on_a_cpu():
    for facts in (mac(8), mac(96), windows(), windows(vram=2)):
        for preference in ("auto", "cpu"):
            plan = plan_for(facts, preference)
            if plan.tracker.startswith("cpu"):
                assert plan.tracker_dtype == "float32"


def test_device_strings_end_in_a_digit_for_vegas_gpu_log_line():
    for facts in (mac(8), mac(96), windows(), windows(vram=8, name="RTX")):
        plan = plan_for(facts)
        assert plan.tracker[-1].isdigit() and plan.exemplar[-1].isdigit()


def test_windows_small_card_keeps_find_similar_on_cpu():
    plan = plan_for(windows(vram=8, name="NVIDIA GeForce RTX 4060"))
    assert (plan.tracker, plan.exemplar, plan.tracker_dtype) == ("cuda:0", "cpu:0", "bfloat16")
    assert "RTX 4060" in plan.label


def test_windows_big_card_runs_both():
    plan = plan_for(windows(vram=16, name="RTX 4090"))
    assert (plan.tracker, plan.exemplar) == ("cuda:0", "cuda:0")


def test_windows_tiny_or_no_card_uses_cpu():
    assert plan_for(windows(vram=2)).tracker == "cpu:0"
    assert plan_for(windows()).tracker == "cpu:0"


def test_a_96_gb_mac_reads_as_96_not_103(monkeypatch):
    from vega_annotator import device
    monkeypatch.setattr(device, "_sysctl_int", lambda name: 103079215104 if name == "hw.memsize" else 12)
    monkeypatch.setattr(device.platform_module, "system", lambda: "Darwin")
    monkeypatch.setattr(device.platform_module, "machine", lambda: "arm64")
    facts = device.read_machine_facts()
    assert facts.ram_gb == 96 and facts.physical_cores == 12


def test_unknown_preference_raises():
    with pytest.raises(ValueError):
        plan_for(mac(8), "turbo")


def test_parse_nvidia_smi_picks_largest_and_survives_junk():
    assert parse_nvidia_smi("NVIDIA GeForce RTX 4060, 8188\nRTX 4090, 24564\n") == ("RTX 4090", pytest.approx(23.99, abs=0.01))
    assert parse_nvidia_smi("") == ("", 0.0)
    assert parse_nvidia_smi("garbage\n,\nname, notanumber\n") == ("", 0.0)
    assert parse_nvidia_smi(None) == ("", 0.0)


def test_intel_mac_is_refused_with_a_reason():
    assert "Intel" in unsupported_reason(MachineFacts("Darwin", "x86_64", 16, 4))
    assert unsupported_reason(mac(8)) == ""
    assert unsupported_reason(windows()) == ""
