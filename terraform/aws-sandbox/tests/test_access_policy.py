"""The CloudFormation access role grants what Runtm needs to pass a template's role.

Renders ``cloudformation/v2/runtm-sandbox.yaml`` with realistic parameters
(resolving ``!Sub``, ``!Ref``, ``!If`` and ``!GetAtt`` the way CloudFormation
would) and checks the access role's policy. The Terraform twin is covered by
``access_policy.tftest.hcl``; both must grant the same thing.

Run: ``pytest terraform/aws-sandbox/tests``
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "cloudformation" / "v2" / "runtm-sandbox.yaml"
ACCOUNT = "123456789012"
REGION = "us-east-1"
PARAMETERS = {"TenantSlug": "cd2675bcb083", "Suffix": "pci", "OrganizationId": "org_123"}


class _Tag:
    def __init__(self, name: str, value: Any) -> None:
        self.name, self.value = name, value


class _Loader(yaml.SafeLoader):
    pass


def _construct(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> _Tag:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return _Tag(suffix, value)


_Loader.add_multi_constructor("!", _construct)


class _Stack:
    def __init__(self, template: dict, parameters: dict[str, str]) -> None:
        self.template = template
        self.values = {
            **{k: str(v.get("Default", "")) for k, v in template["Parameters"].items()},
            **parameters,
            "AWS::AccountId": ACCOUNT,
            "AWS::Region": REGION,
            "AWS::Partition": "aws",
        }
        self.conditions = {
            name: self.resolve(expr) for name, expr in template.get("Conditions", {}).items()
        }

    def resource(self, name: str) -> dict:
        return self.resolve(self.template["Resources"][name])

    def role_arn(self, name: str) -> str:
        return f"arn:aws:iam::{ACCOUNT}:role/{self.resource(name)['Properties']['RoleName']}"

    def get_att(self, resource: str, attribute: str) -> str:
        """A role's real ARN; a stand-in for anything else (the bucket ARN)."""
        if attribute == "Arn" and self.template["Resources"][resource]["Type"] == "AWS::IAM::Role":
            return self.role_arn(resource)
        return f"<{resource}.{attribute}>"

    def resolve(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {k: self.resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.resolve(v) for v in node]
        if not isinstance(node, _Tag):
            return node
        if node.name == "Ref":
            return self.values[node.value]
        if node.name == "Sub":
            text, extra = (node.value, {}) if isinstance(node.value, str) else node.value
            scope = {**self.values, **{k: self.resolve(v) for k, v in extra.items()}}

            def value(name: str) -> str:
                return str(scope[name]) if name in scope else self.get_att(*name.split(".", 1))

            return re.sub(r"\$\{([^}!]+)\}", lambda m: value(m.group(1)), text)
        if node.name == "GetAtt":
            resource, attribute = (
                node.value.split(".", 1) if isinstance(node.value, str) else node.value
            )
            return self.get_att(resource, attribute)
        if node.name == "If":
            name, then, otherwise = node.value
            return self.resolve(then if self.conditions[name] else otherwise)
        if node.name == "Equals":
            left, right = (self.resolve(v) for v in node.value)
            return left == right
        if node.name == "Not":
            return not self.resolve(node.value[0])
        raise AssertionError(f"unsupported intrinsic !{node.name}")


@pytest.fixture(scope="module")
def stack() -> _Stack:
    return _Stack(yaml.load(TEMPLATE.read_text(), Loader=_Loader), PARAMETERS)


@pytest.fixture(scope="module")
def access_statements(stack: _Stack) -> dict[str, dict]:
    role = stack.resource("RuntmSandboxAccessRole")["Properties"]
    (policy,) = [p for p in role["Policies"] if p["PolicyName"] == "runtm-sandbox-access"]
    return {s["Sid"]: s for s in policy["PolicyDocument"]["Statement"] if "Sid" in s}


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def test_runtm_may_pass_its_own_roles_and_template_roles(stack, access_statements):
    statement = access_statements["PassMicrovmRoles"]
    assert statement["Effect"] == "Allow"
    assert _as_list(statement["Action"]) == ["iam:PassRole"]
    assert sorted(_as_list(statement["Resource"])) == sorted(
        [
            stack.role_arn("MicrovmBuildRole"),
            stack.role_arn("MicrovmExecutionRole"),
            f"arn:aws:iam::{ACCOUNT}:role/runtm-sandbox/*",
        ]
    )


def test_a_template_role_under_the_path_matches_and_others_do_not(access_statements):
    patterns = _as_list(access_statements["PassMicrovmRoles"]["Resource"])

    def allowed(arn: str) -> bool:
        return any(re.fullmatch(re.escape(p).replace(r"\*", ".*"), arn) for p in patterns)

    assert allowed(f"arn:aws:iam::{ACCOUNT}:role/runtm-sandbox/billing-agent")
    assert not allowed(f"arn:aws:iam::{ACCOUNT}:role/billing-agent")
    assert not allowed(f"arn:aws:iam::{ACCOUNT}:role/admin/runtm-sandbox/billing-agent")
    assert not allowed("arn:aws:iam::999999999999:role/runtm-sandbox/billing-agent")


def test_runtm_may_read_only_its_own_role_tags(stack, access_statements):
    statement = access_statements["ReadOwnStackRevision"]
    assert statement["Effect"] == "Allow"
    assert _as_list(statement["Action"]) == ["iam:ListRoleTags"]
    (pattern,) = _as_list(statement["Resource"])
    assert pattern == f"arn:aws:iam::{ACCOUNT}:role/RuntmSandboxAccessRole-*"
    assert stack.role_arn("RuntmSandboxAccessRole").startswith(pattern[:-1])


def test_the_access_role_carries_the_stack_revision(stack):
    tags = stack.resource("RuntmSandboxAccessRole")["Properties"]["Tags"]
    assert {t["Key"]: t["Value"] for t in tags}["runtm:stack-revision"] == "2"


def test_terraform_and_cloudformation_stamp_the_same_revision():
    main_tf = (Path(__file__).resolve().parents[1] / "main.tf").read_text()
    (tf_revision,) = re.findall(r'"runtm:stack-revision"\s*=\s*"(\d+)"', main_tf)
    (cf_revision,) = re.findall(r'runtm:stack-revision, Value: "(\d+)"', TEMPLATE.read_text())
    assert tf_revision == cf_revision
