"""Tests for runtm_shared.manifest."""

import pytest

from runtm_shared.manifest import Connection, EnvVar, EnvVarType, Manifest, Policy


class TestManifestParsing:
    """Tests for manifest parsing."""

    def test_valid_minimal_manifest(self) -> None:
        """Parse a minimal valid manifest."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-service"
        assert manifest.template == "backend-service"
        assert manifest.runtime == "python"
        assert manifest.health_path == "/health"  # default
        assert manifest.port == 8080  # default

    def test_valid_full_manifest(self) -> None:
        """Parse a full manifest with all fields."""
        yaml_content = """
name: my-custom-service
template: backend-service
runtime: python
health_path: /healthz
port: 3000
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-custom-service"
        assert manifest.health_path == "/healthz"
        assert manifest.port == 3000

    def test_missing_required_field(self) -> None:
        """Missing required field should raise error."""
        yaml_content = """
name: my-service
template: backend-service
"""
        with pytest.raises(ValueError):
            Manifest.from_yaml(yaml_content)

    def test_invalid_yaml(self) -> None:
        """Invalid YAML should raise error."""
        yaml_content = "invalid: yaml: content:"
        with pytest.raises(ValueError, match="Invalid YAML"):
            Manifest.from_yaml(yaml_content)


class TestManifestValidation:
    """Tests for manifest field validation."""

    def test_name_lowercase(self) -> None:
        """Name should be converted to lowercase."""
        yaml_content = """
name: My-Service
template: backend-service
runtime: python
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-service"

    def test_name_too_long(self) -> None:
        """Name over 63 characters should fail."""
        yaml_content = f"""
name: {"a" * 64}
template: backend-service
runtime: python
"""
        with pytest.raises(ValueError, match="63 characters"):
            Manifest.from_yaml(yaml_content)

    def test_name_invalid_characters(self) -> None:
        """Name with invalid characters should fail."""
        yaml_content = """
name: my_service@v1
template: backend-service
runtime: python
"""
        with pytest.raises(ValueError, match="lowercase letters"):
            Manifest.from_yaml(yaml_content)

    def test_name_must_start_alphanumeric(self) -> None:
        """Name must start with alphanumeric."""
        yaml_content = """
name: -my-service
template: backend-service
runtime: python
"""
        with pytest.raises(ValueError, match="start with"):
            Manifest.from_yaml(yaml_content)

    def test_name_must_end_alphanumeric(self) -> None:
        """Name must end with alphanumeric."""
        yaml_content = """
name: my-service-
template: backend-service
runtime: python
"""
        with pytest.raises(ValueError, match="end with"):
            Manifest.from_yaml(yaml_content)

    def test_invalid_template(self) -> None:
        """Invalid template should fail."""
        yaml_content = """
name: my-service
template: invalid-template
runtime: python
"""
        with pytest.raises(ValueError, match="template must be one of"):
            Manifest.from_yaml(yaml_content)

    def test_invalid_runtime(self) -> None:
        """Invalid runtime should fail."""
        yaml_content = """
name: my-service
template: backend-service
runtime: ruby
"""
        with pytest.raises(ValueError, match="runtime must be one of"):
            Manifest.from_yaml(yaml_content)

    def test_invalid_port(self) -> None:
        """Invalid port should fail."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
port: 70000
"""
        with pytest.raises(ValueError, match="port must be"):
            Manifest.from_yaml(yaml_content)

    def test_health_path_must_start_with_slash(self) -> None:
        """Health path must start with /."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
health_path: health
"""
        with pytest.raises(ValueError, match="start with /"):
            Manifest.from_yaml(yaml_content)


class TestSecretsRejection:
    """Tests for V0 secrets/env rejection - unknown fields are rejected."""

    def test_env_rejected(self) -> None:
        """Manifest with env should be rejected (extra fields not permitted)."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
env:
  API_KEY: secret
"""
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            Manifest.from_yaml(yaml_content)

    def test_secrets_rejected(self) -> None:
        """Manifest with secrets should be rejected (extra fields not permitted)."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
secrets:
  - API_KEY
"""
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            Manifest.from_yaml(yaml_content)


class TestManifestSerialization:
    """Tests for manifest serialization."""

    def test_to_yaml(self) -> None:
        """Manifest should serialize to YAML."""
        manifest = Manifest(
            name="my-service",
            template="backend-service",
            runtime="python",
        )
        yaml_output = manifest.to_yaml()
        assert "name: my-service" in yaml_output
        assert "template: backend-service" in yaml_output

    def test_to_dict(self) -> None:
        """Manifest should convert to dict."""
        manifest = Manifest(
            name="my-service",
            template="backend-service",
            runtime="python",
        )
        d = manifest.to_dict()
        assert d["name"] == "my-service"
        assert d["template"] == "backend-service"
        assert d["runtime"] == "python"


class TestStaticSiteTemplate:
    """Tests for static-site template support."""

    def test_valid_static_site_manifest(self) -> None:
        """Parse a valid static-site template manifest."""
        yaml_content = """
name: my-site
template: static-site
runtime: node
health_path: /health
port: 3000
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-site"
        assert manifest.template == "static-site"
        assert manifest.runtime == "node"
        assert manifest.health_path == "/health"
        assert manifest.port == 3000

    def test_static_site_with_defaults(self) -> None:
        """Static-site template with default values."""
        yaml_content = """
name: my-site
template: static-site
runtime: node
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-site"
        assert manifest.template == "static-site"
        assert manifest.runtime == "node"
        assert manifest.health_path == "/health"
        assert manifest.port == 8080  # default port


class TestWebAppTemplate:
    """Tests for web-app template support."""

    def test_valid_web_app_manifest(self) -> None:
        """Parse a valid web-app template manifest with standard tier."""
        yaml_content = """
name: my-web-app
template: web-app
runtime: fullstack
health_path: /health
port: 3000
tier: standard
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-web-app"
        assert manifest.template == "web-app"
        assert manifest.runtime == "fullstack"
        assert manifest.health_path == "/health"
        assert manifest.port == 3000
        assert manifest.tier == "standard"

    def test_fullstack_rejects_starter_tier(self) -> None:
        """Fullstack apps with starter tier should be rejected."""
        yaml_content = """
name: my-web-app
template: web-app
runtime: fullstack
tier: starter
"""
        with pytest.raises(ValueError, match="Fullstack apps require at least 'standard' tier"):
            Manifest.from_yaml(yaml_content)

    def test_fullstack_accepts_performance_tier(self) -> None:
        """Fullstack apps with performance tier should be accepted."""
        yaml_content = """
name: my-web-app
template: web-app
runtime: fullstack
tier: performance
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.tier == "performance"


class TestEnvVarValidation:
    """Tests for EnvVar model validation."""

    def test_valid_env_var(self) -> None:
        """Should accept valid env var."""
        env_var = EnvVar(
            name="DATABASE_URL",
            type=EnvVarType.STRING,
            required=True,
            secret=True,
            description="PostgreSQL connection string",
        )
        assert env_var.name == "DATABASE_URL"
        assert env_var.type == EnvVarType.STRING
        assert env_var.required is True
        assert env_var.secret is True

    def test_env_var_name_validation(self) -> None:
        """Should validate env var name format."""
        # Valid names
        EnvVar(name="MY_VAR")
        EnvVar(name="MY_VAR_123")
        EnvVar(name="A")

        # Invalid: empty name
        with pytest.raises(ValueError, match="cannot be empty"):
            EnvVar(name="")

        # Invalid: starts with number
        with pytest.raises(ValueError, match="must start with a letter"):
            EnvVar(name="123_VAR")

        # Invalid: contains invalid characters
        with pytest.raises(ValueError, match="alphanumeric with underscores"):
            EnvVar(name="MY-VAR")

    def test_secret_env_var_cannot_have_default(self) -> None:
        """Secret env vars should not have default values."""
        with pytest.raises(ValueError, match="cannot have a default value"):
            EnvVar(
                name="API_KEY",
                secret=True,
                default="default_value",
            )

    def test_non_secret_env_var_can_have_default(self) -> None:
        """Non-secret env vars can have default values."""
        env_var = EnvVar(
            name="LOG_LEVEL",
            secret=False,
            default="INFO",
        )
        assert env_var.default == "INFO"

    def test_env_var_types(self) -> None:
        """Should support all env var types."""
        for env_type in EnvVarType:
            env_var = EnvVar(name="MY_VAR", type=env_type)
            assert env_var.type == env_type


class TestConnectionValidation:
    """Tests for Connection model validation."""

    def test_valid_connection(self) -> None:
        """Should accept valid connection."""
        conn = Connection(
            name="supabase",
            env_vars=["SUPABASE_URL", "SUPABASE_ANON_KEY"],
        )
        assert conn.name == "supabase"
        assert len(conn.env_vars) == 2

    def test_connection_name_lowercased(self) -> None:
        """Connection name should be lowercased."""
        conn = Connection(name="MyConnection", env_vars=["VAR"])
        assert conn.name == "myconnection"

    def test_connection_name_validation(self) -> None:
        """Should validate connection name format."""
        # Valid names
        Connection(name="my-connection", env_vars=["VAR"])
        Connection(name="my_connection", env_vars=["VAR"])

        # Invalid: empty name
        with pytest.raises(ValueError, match="cannot be empty"):
            Connection(name="", env_vars=["VAR"])

        # Invalid: special characters
        with pytest.raises(ValueError, match="alphanumeric"):
            Connection(name="my@connection", env_vars=["VAR"])

    def test_connection_requires_env_vars(self) -> None:
        """Connection must have at least one env var."""
        with pytest.raises(ValueError, match="at least one env var"):
            Connection(name="empty", env_vars=[])


class TestEnvSchemaInManifest:
    """Tests for env_schema in Manifest."""

    def test_manifest_with_env_schema(self) -> None:
        """Should parse manifest with env_schema."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
env_schema:
  - name: DATABASE_URL
    type: string
    required: true
    secret: true
    description: PostgreSQL connection string
  - name: LOG_LEVEL
    type: string
    required: false
    default: INFO
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert len(manifest.env_schema) == 2
        assert manifest.env_schema[0].name == "DATABASE_URL"
        assert manifest.env_schema[0].secret is True
        assert manifest.env_schema[1].name == "LOG_LEVEL"
        assert manifest.env_schema[1].default == "INFO"

    def test_manifest_with_connections(self) -> None:
        """Should parse manifest with connections."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
env_schema:
  - name: SUPABASE_URL
    type: string
    required: true
  - name: SUPABASE_ANON_KEY
    type: string
    required: true
    secret: true
connections:
  - name: supabase
    env_vars: [SUPABASE_URL, SUPABASE_ANON_KEY]
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert len(manifest.connections) == 1
        assert manifest.connections[0].name == "supabase"
        assert len(manifest.connections[0].env_vars) == 2

    def test_connection_must_reference_declared_env_vars(self) -> None:
        """Connection env_vars must be declared in env_schema."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
env_schema:
  - name: DECLARED_VAR
    type: string
connections:
  - name: my-conn
    env_vars: [UNDECLARED_VAR]
"""
        with pytest.raises(ValueError, match="undeclared env var"):
            Manifest.from_yaml(yaml_content)

    def test_get_secret_env_vars(self) -> None:
        """Should return only secret env vars."""
        manifest = Manifest(
            name="test",
            template="backend-service",
            runtime="python",
            env_schema=[
                EnvVar(name="SECRET_VAR", secret=True),
                EnvVar(name="PUBLIC_VAR", secret=False),
                EnvVar(name="ANOTHER_SECRET", secret=True),
            ],
        )
        secrets = manifest.get_secret_env_vars()
        assert len(secrets) == 2
        assert all(ev.secret for ev in secrets)

    def test_get_required_env_vars(self) -> None:
        """Should return only required env vars."""
        manifest = Manifest(
            name="test",
            template="backend-service",
            runtime="python",
            env_schema=[
                EnvVar(name="REQUIRED_VAR", required=True),
                EnvVar(name="OPTIONAL_VAR", required=False),
                EnvVar(name="ANOTHER_REQUIRED", required=True),
            ],
        )
        required = manifest.get_required_env_vars()
        assert len(required) == 2
        assert all(ev.required for ev in required)

    def test_get_connection_env_vars(self) -> None:
        """Should return env vars for a named connection."""
        manifest = Manifest(
            name="test",
            template="backend-service",
            runtime="python",
            env_schema=[
                EnvVar(name="SUPABASE_URL"),
                EnvVar(name="SUPABASE_KEY"),
                EnvVar(name="OTHER_VAR"),
            ],
            connections=[
                Connection(name="supabase", env_vars=["SUPABASE_URL", "SUPABASE_KEY"]),
            ],
        )
        conn_vars = manifest.get_connection_env_vars("supabase")
        assert conn_vars == ["SUPABASE_URL", "SUPABASE_KEY"]

        # Non-existent connection
        assert manifest.get_connection_env_vars("nonexistent") == []


class TestPolicyInManifest:
    """Tests for policy in Manifest."""

    def test_manifest_with_policy(self) -> None:
        """Should parse manifest with policy."""
        yaml_content = """
name: my-service
template: backend-service
runtime: python
policy:
  mode: sandbox
  egress: allowlist
  egress_allowlist:
    - api.example.com
    - cdn.example.com
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.policy is not None
        assert manifest.policy.mode.value == "sandbox"
        assert manifest.policy.egress == "allowlist"
        assert len(manifest.policy.egress_allowlist) == 2

    def test_policy_egress_validation(self) -> None:
        """Should validate egress mode."""
        # Valid modes
        Policy(egress="public")
        Policy(egress="allowlist")

        # Invalid mode
        with pytest.raises(ValueError, match="egress must be one of"):
            Policy(egress="invalid")

    def test_env_schema_serialization(self) -> None:
        """Should serialize env_schema to YAML correctly."""
        manifest = Manifest(
            name="test",
            template="backend-service",
            runtime="python",
            env_schema=[
                EnvVar(
                    name="DATABASE_URL",
                    type=EnvVarType.STRING,
                    required=True,
                    secret=True,
                ),
            ],
        )
        yaml_output = manifest.to_yaml()
        assert "env_schema:" in yaml_output
        assert "DATABASE_URL" in yaml_output
        assert "secret: true" in yaml_output

    def test_connections_serialization(self) -> None:
        """Should serialize connections to YAML correctly."""
        manifest = Manifest(
            name="test",
            template="backend-service",
            runtime="python",
            env_schema=[
                EnvVar(name="SUPABASE_URL"),
            ],
            connections=[
                Connection(name="supabase", env_vars=["SUPABASE_URL"]),
            ],
        )
        yaml_output = manifest.to_yaml()
        assert "connections:" in yaml_output
        assert "supabase" in yaml_output


class TestDockerTemplate:
    """Tests for docker template support (bring your own Dockerfile)."""

    def test_docker_template_no_runtime(self) -> None:
        """Docker template doesn't require runtime."""
        yaml_content = """
name: my-go-api
template: docker
port: 8080
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-go-api"
        assert manifest.template == "docker"
        assert manifest.runtime is None
        assert manifest.port == 8080

    def test_docker_template_with_runtime_ignored(self) -> None:
        """Docker template can have runtime specified (but it's ignored)."""
        yaml_content = """
name: my-rust-api
template: docker
runtime: python
port: 8080
"""
        # Runtime is accepted but semantically ignored for docker template
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.template == "docker"
        assert manifest.runtime == "python"  # Set but not enforced

    def test_docker_template_with_all_options(self) -> None:
        """Docker template with all common options."""
        yaml_content = """
name: my-custom-service
template: docker
port: 9000
health_path: /healthz
tier: standard
"""
        manifest = Manifest.from_yaml(yaml_content)
        assert manifest.name == "my-custom-service"
        assert manifest.template == "docker"
        assert manifest.runtime is None
        assert manifest.port == 9000
        assert manifest.health_path == "/healthz"
        assert manifest.tier == "standard"

    def test_non_docker_requires_runtime(self) -> None:
        """Non-docker templates require runtime."""
        yaml_content = """
name: my-service
template: backend-service
"""
        with pytest.raises(ValueError, match="runtime is required"):
            Manifest.from_yaml(yaml_content)

    def test_static_site_requires_runtime(self) -> None:
        """Static-site template requires runtime."""
        yaml_content = """
name: my-site
template: static-site
"""
        with pytest.raises(ValueError, match="runtime is required"):
            Manifest.from_yaml(yaml_content)

    def test_web_app_requires_runtime(self) -> None:
        """Web-app template requires runtime."""
        yaml_content = """
name: my-app
template: web-app
tier: standard
"""
        with pytest.raises(ValueError, match="runtime is required"):
            Manifest.from_yaml(yaml_content)

    def test_docker_template_to_dict_no_runtime(self) -> None:
        """Docker template to_dict should not include runtime if None."""
        manifest = Manifest(
            name="my-docker-app",
            template="docker",
            port=8080,
        )
        data = manifest.to_dict()
        assert data["name"] == "my-docker-app"
        assert data["template"] == "docker"
        assert "runtime" not in data

    def test_docker_template_to_yaml(self) -> None:
        """Docker template should serialize to YAML without runtime."""
        manifest = Manifest(
            name="my-docker-app",
            template="docker",
            port=8080,
        )
        yaml_output = manifest.to_yaml()
        assert "template: docker" in yaml_output
        assert "runtime:" not in yaml_output
