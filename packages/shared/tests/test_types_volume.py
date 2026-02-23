"""Tests for VolumeConfig and MachineTier specs in types."""

from runtm_shared.types import (
    MACHINE_TIER_SPECS,
    MachineConfig,
    MachineTier,
    VolumeConfig,
    get_tier_spec,
)


class TestVolumeConfig:
    """Test the VolumeConfig dataclass."""

    def test_create_volume_config(self):
        """Can create VolumeConfig."""
        vol = VolumeConfig(name="data", path="/data", size_gb=1)
        assert vol.name == "data"
        assert vol.path == "/data"
        assert vol.size_gb == 1

    def test_volume_config_default_size(self):
        """VolumeConfig has default size of 1GB."""
        vol = VolumeConfig(name="data", path="/data")
        assert vol.size_gb == 1


class TestMachineTierSpecs:
    """Test that all tier specifications are correct."""

    def test_four_tiers_exist(self):
        """There should be exactly 4 tiers."""
        assert len(MachineTier) == 4
        assert len(MACHINE_TIER_SPECS) == 4

    def test_starter_spec(self):
        spec = get_tier_spec(MachineTier.STARTER)
        assert spec.memory_mb == 512
        assert spec.cpus == 1
        assert spec.cpu_kind == "shared"

    def test_standard_spec(self):
        spec = get_tier_spec(MachineTier.STANDARD)
        assert spec.memory_mb == 2048
        assert spec.cpus == 2
        assert spec.cpu_kind == "shared"

    def test_performance_spec(self):
        spec = get_tier_spec(MachineTier.PERFORMANCE)
        assert spec.memory_mb == 4096
        assert spec.cpus == 4
        assert spec.cpu_kind == "shared"

    def test_pro_spec(self):
        spec = get_tier_spec(MachineTier.PRO)
        assert spec.memory_mb == 8192
        assert spec.cpus == 4
        assert spec.cpu_kind == "shared"

    def test_tiers_are_ordered_by_memory(self):
        """Each tier should have strictly more memory than the previous."""
        tiers = [MachineTier.STARTER, MachineTier.STANDARD, MachineTier.PERFORMANCE, MachineTier.PRO]
        for i in range(1, len(tiers)):
            prev = get_tier_spec(tiers[i - 1])
            curr = get_tier_spec(tiers[i])
            assert curr.memory_mb > prev.memory_mb, f"{tiers[i].value} should have more RAM than {tiers[i-1].value}"


class TestMachineConfigFromTier:
    """Test MachineConfig.from_tier with updated tier specs."""

    def test_from_starter(self):
        config = MachineConfig.from_tier(tier=MachineTier.STARTER, image="test:latest")
        assert config.memory_mb == 512
        assert config.cpus == 1

    def test_from_standard(self):
        config = MachineConfig.from_tier(tier=MachineTier.STANDARD, image="test:latest")
        assert config.memory_mb == 2048
        assert config.cpus == 2

    def test_from_performance(self):
        config = MachineConfig.from_tier(tier=MachineTier.PERFORMANCE, image="test:latest")
        assert config.memory_mb == 4096
        assert config.cpus == 4

    def test_from_pro(self):
        config = MachineConfig.from_tier(tier=MachineTier.PRO, image="test:latest")
        assert config.memory_mb == 8192
        assert config.cpus == 4


class TestMachineConfigWithVolumes:
    """Test MachineConfig with volumes."""

    def test_machine_config_no_volumes_by_default(self):
        """MachineConfig has empty volumes by default."""
        config = MachineConfig(image="test:latest")
        assert config.volumes == []

    def test_machine_config_with_volumes(self):
        """MachineConfig can have volumes."""
        volumes = [
            VolumeConfig(name="data", path="/data", size_gb=1),
            VolumeConfig(name="cache", path="/cache", size_gb=5),
        ]
        config = MachineConfig(image="test:latest", volumes=volumes)
        assert len(config.volumes) == 2
        assert config.volumes[0].name == "data"
        assert config.volumes[1].name == "cache"

    def test_from_tier_with_volumes(self):
        """MachineConfig.from_tier works with volumes."""
        volumes = [VolumeConfig(name="data", path="/data", size_gb=1)]
        config = MachineConfig.from_tier(
            tier=MachineTier.STANDARD,
            image="test:latest",
            volumes=volumes,
        )
        assert config.memory_mb == 2048
        assert len(config.volumes) == 1
        assert config.volumes[0].name == "data"

    def test_from_tier_default_no_volumes(self):
        """MachineConfig.from_tier defaults to no volumes."""
        config = MachineConfig.from_tier(
            tier=MachineTier.STANDARD,
            image="test:latest",
        )
        assert config.volumes == []
