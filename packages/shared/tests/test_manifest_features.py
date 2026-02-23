"""Tests for manifest features (database, auth) and volumes."""

import pytest
from pydantic import ValidationError

from runtm_shared.manifest import Features, Manifest, VolumeMount


class TestFeatures:
    """Test the Features model."""

    def test_default_features_disabled(self):
        """Default features are disabled."""
        features = Features()
        assert features.database is False
        assert features.auth is False

    def test_enable_database(self):
        """Can enable database feature."""
        features = Features(database=True)
        assert features.database is True
        assert features.auth is False

    def test_enable_auth(self):
        """Can enable auth feature."""
        features = Features(database=True, auth=True)
        assert features.database is True
        assert features.auth is True

    def test_extra_fields_forbidden(self):
        """Extra fields are not allowed."""
        with pytest.raises(ValidationError):
            Features(database=True, invalid_field=True)


class TestVolumeMount:
    """Test the VolumeMount model."""

    def test_default_volume(self):
        """Default volume configuration."""
        vol = VolumeMount()
        assert vol.name == "data"
        assert vol.path == "/data"
        assert vol.size_gb == 1

    def test_custom_volume(self):
        """Custom volume configuration."""
        vol = VolumeMount(name="storage", path="/app/storage", size_gb=5)
        assert vol.name == "storage"
        assert vol.path == "/app/storage"
        assert vol.size_gb == 5

    def test_volume_name_lowercased(self):
        """Volume name is lowercased."""
        vol = VolumeMount(name="MyData")
        assert vol.name == "mydata"

    def test_volume_name_validation(self):
        """Volume name must be alphanumeric with underscores/hyphens."""
        with pytest.raises(ValidationError):
            VolumeMount(name="")
        with pytest.raises(ValidationError):
            VolumeMount(name="invalid name")  # spaces
        with pytest.raises(ValidationError):
            VolumeMount(name="a" * 31)  # too long

    def test_volume_path_must_be_absolute(self):
        """Volume path must start with /."""
        with pytest.raises(ValidationError):
            VolumeMount(path="relative/path")

    def test_volume_path_cannot_be_root(self):
        """Volume path cannot be root."""
        with pytest.raises(ValidationError):
            VolumeMount(path="/")

    def test_volume_size_bounds(self):
        """Volume size must be between 1 and 100 GB."""
        with pytest.raises(ValidationError):
            VolumeMount(size_gb=0)
        with pytest.raises(ValidationError):
            VolumeMount(size_gb=101)
        # Valid bounds
        vol_min = VolumeMount(size_gb=1)
        vol_max = VolumeMount(size_gb=100)
        assert vol_min.size_gb == 1
        assert vol_max.size_gb == 100


class TestManifestWithFeatures:
    """Test manifest with features enabled."""

    def test_manifest_default_no_features(self):
        """Manifest defaults to no features enabled."""
        manifest = Manifest(
            name="test-app",
            template="backend-service",
            runtime="python",
        )
        assert manifest.features.database is False
        assert manifest.features.auth is False
        assert manifest.volumes == []

    def test_database_feature_auto_creates_volume(self):
        """Enabling database feature auto-creates volume."""
        manifest = Manifest(
            name="test-app",
            template="backend-service",
            runtime="python",
            features=Features(database=True),
        )
        assert manifest.features.database is True
        assert len(manifest.volumes) == 1
        assert manifest.volumes[0].name == "data"
        assert manifest.volumes[0].path == "/data"
        assert manifest.volumes[0].size_gb == 1

    def test_database_feature_preserves_custom_volumes(self):
        """Enabling database doesn't override custom volumes."""
        manifest = Manifest(
            name="test-app",
            template="backend-service",
            runtime="python",
            features=Features(database=True),
            volumes=[VolumeMount(name="custom", path="/custom", size_gb=5)],
        )
        assert len(manifest.volumes) == 1
        assert manifest.volumes[0].name == "custom"

    def test_auth_requires_database(self):
        """Auth feature requires database feature."""
        with pytest.raises(ValidationError) as exc_info:
            Manifest(
                name="test-app",
                template="web-app",
                runtime="fullstack",
                tier="starter",
                features=Features(auth=True),  # database=False
            )
        assert "features.database" in str(exc_info.value)

    def test_auth_requires_web_app_template(self):
        """Auth feature only works with web-app template."""
        with pytest.raises(ValidationError) as exc_info:
            Manifest(
                name="test-app",
                template="backend-service",  # not web-app
                runtime="python",
                features=Features(database=True, auth=True),
                env_schema=[
                    {
                        "name": "AUTH_SECRET",
                        "type": "string",
                        "required": True,
                        "secret": True,
                    }
                ],
            )
        assert "web-app" in str(exc_info.value)

    def test_auth_requires_auth_secret(self):
        """Auth feature requires AUTH_SECRET env var."""
        with pytest.raises(ValidationError) as exc_info:
            Manifest(
                name="test-app",
                template="web-app",
                runtime="fullstack",
                tier="starter",
                features=Features(database=True, auth=True),
                # No AUTH_SECRET defined
            )
        assert "AUTH_SECRET" in str(exc_info.value)

    def test_auth_with_all_requirements(self):
        """Auth feature works when all requirements met."""
        manifest = Manifest(
            name="test-app",
            template="web-app",
            runtime="fullstack",
            tier="starter",
            features=Features(database=True, auth=True),
            env_schema=[
                {
                    "name": "AUTH_SECRET",
                    "type": "string",
                    "required": True,
                    "secret": True,
                }
            ],
        )
        assert manifest.features.auth is True
        assert manifest.features.database is True
        assert len(manifest.volumes) == 1


class TestManifestFeaturesYaml:
    """Test manifest features YAML serialization."""

    def test_features_to_yaml(self):
        """Features are serialized to YAML correctly."""
        manifest = Manifest(
            name="test-app",
            template="backend-service",
            runtime="python",
            features=Features(database=True),
        )
        data = manifest.to_dict()
        assert "features" in data
        assert data["features"]["database"] is True
        assert "auth" not in data["features"]  # False values not included

    def test_features_from_yaml(self):
        """Features are parsed from YAML correctly."""
        yaml_content = """
name: test-app
template: backend-service
runtime: python
features:
  database: true
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.features.database is True
        assert manifest.features.auth is False

    def test_volumes_to_yaml(self):
        """Volumes are serialized to YAML correctly."""
        manifest = Manifest(
            name="test-app",
            template="backend-service",
            runtime="python",
            features=Features(database=True),
        )
        data = manifest.to_dict()
        assert "volumes" in data
        assert len(data["volumes"]) == 1
        assert data["volumes"][0]["name"] == "data"

    def test_volumes_from_yaml(self):
        """Volumes are parsed from YAML correctly."""
        yaml_content = """
name: test-app
template: backend-service
runtime: python
volumes:
  - name: storage
    path: /storage
    size_gb: 10
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert len(manifest.volumes) == 1
        assert manifest.volumes[0].name == "storage"
        assert manifest.volumes[0].size_gb == 10


class TestManifestAuthFullExample:
    """Full example of auth-enabled manifest."""

    def test_full_auth_manifest(self):
        """Complete auth-enabled manifest validates."""
        yaml_content = """
name: my-app
template: web-app
runtime: fullstack
tier: starter
features:
  database: true
  auth: true
env_schema:
  - name: AUTH_SECRET
    type: string
    required: true
    secret: true
    description: "Secret for signing auth cookies/sessions"
  - name: DATABASE_URL
    type: string
    required: false
    secret: true
    description: "Optional external Postgres URL"
  - name: GOOGLE_CLIENT_ID
    type: string
    required: false
    description: "Google OAuth client ID"
  - name: GOOGLE_CLIENT_SECRET
    type: string
    required: false
    secret: true
    description: "Google OAuth client secret"
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-app"
        assert manifest.features.database is True
        assert manifest.features.auth is True
        assert len(manifest.volumes) == 1
        assert len(manifest.env_schema) == 4
