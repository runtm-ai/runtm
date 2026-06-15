"""Tests for template.py CLI command — session-arg spec parsing."""

import pytest
import typer

from runtm_cli.commands.template import _parse_arg_spec, _parse_args


class TestParseArgSpec:
    """Tests for parsing a single --arg spec into a session-arg dict."""

    def test_minimal_spec_defaults_to_text(self) -> None:
        result = _parse_arg_spec("key=BRANCH")
        assert result == {
            "key": "BRANCH",
            "label": "BRANCH",  # defaults to key
            "type": "text",
            "required": False,
        }

    def test_full_select_spec(self) -> None:
        result = _parse_arg_spec(
            "key=BRANCH,label=Git Branch,type=select,"
            "options=main|staging|prod,default=main,required=true"
        )
        assert result == {
            "key": "BRANCH",
            "label": "Git Branch",
            "type": "select",
            "required": True,
            "default": "main",
            "options": ["main", "staging", "prod"],
        }

    def test_boolean_spec(self) -> None:
        result = _parse_arg_spec("key=DEBUG,type=boolean,default=false")
        assert result["type"] == "boolean"
        assert result["default"] == "false"
        assert "options" not in result

    def test_help_alias(self) -> None:
        result = _parse_arg_spec("key=NOTE,help=some help")
        assert result["help_text"] == "some help"

    def test_help_text_field(self) -> None:
        result = _parse_arg_spec("key=NOTE,help_text=other help")
        assert result["help_text"] == "other help"

    def test_required_truthy_variants(self) -> None:
        for v in ("true", "1", "yes", "on"):
            assert _parse_arg_spec(f"key=X,required={v}")["required"] is True
        for v in ("false", "0", "no", "off"):
            assert _parse_arg_spec(f"key=X,required={v}")["required"] is False

    def test_missing_key_exits(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_arg_spec("label=No Key")

    def test_unknown_field_exits(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_arg_spec("key=X,bogus=1")

    def test_invalid_type_exits(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_arg_spec("key=X,type=number")

    def test_invalid_boolean_exits(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_arg_spec("key=X,required=maybe")

    def test_select_without_options_exits(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_arg_spec("key=X,type=select")

    def test_segment_without_equals_exits(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_arg_spec("key=X,justtext")


class TestParseArgs:
    """Tests for parsing a list of --arg specs."""

    def test_multiple_specs(self) -> None:
        result = _parse_args(
            [
                "key=BRANCH,type=select,options=main|dev",
                "key=DEBUG,type=boolean",
            ]
        )
        assert [a["key"] for a in result] == ["BRANCH", "DEBUG"]

    def test_empty_list(self) -> None:
        assert _parse_args([]) == []

    def test_duplicate_keys_exit(self) -> None:
        with pytest.raises(typer.Exit):
            _parse_args(["key=DUP", "key=DUP"])
