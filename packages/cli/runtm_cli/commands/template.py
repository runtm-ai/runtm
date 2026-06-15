"""Template command - manage org templates and their session arguments.

Talks to the cloud control plane's CLI-facing template API
(``/api/v0/org-templates``). Requires an API key with ``templates:read`` /
``templates:write`` scopes (org admins only) and runs against an organization
— the org is taken from the API key, or overridden with ``--org``.

Session arguments are declared with repeatable ``--arg`` flags using a compact
``field=value`` syntax, comma-separated, e.g.::

    runtm template create "My Template" --repo acme/app \\
      --arg 'key=BRANCH,label=Git Branch,type=select,options=main|staging,default=main,required=true' \\
      --arg 'key=DEBUG,label=Debug mode,type=boolean,default=false'

Fields: ``key`` (required), ``label`` (defaults to key), ``type``
(text|select|boolean, default text), ``required`` (true/false), ``default``,
``options`` (``|``-separated, for select), ``help``/``help_text``. Field values
may not contain commas; use ``|`` to separate select options.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from runtm_shared.errors import RuntmError

from ..api_client import APIClient
from ..config import get_token

console = Console()

_VALID_ARG_TYPES = {"text", "select", "boolean"}
_TRUE_STRINGS = {"true", "1", "yes", "on"}
_FALSE_STRINGS = {"false", "0", "no", "off"}
# Recognized field names inside a --arg spec (maps aliases to schema keys).
_ARG_FIELDS = {
    "key": "key",
    "label": "label",
    "type": "type",
    "required": "required",
    "default": "default",
    "options": "options",
    "help": "help_text",
    "help_text": "help_text",
}


def _require_auth() -> None:
    if not get_token():
        console.print("[red]✗[/red] Not authenticated. Run `runtm login` first.")
        raise typer.Exit(1)


def _parse_bool(value: str, *, field: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE_STRINGS:
        return True
    if v in _FALSE_STRINGS:
        return False
    console.print(f"[red]✗[/red] Invalid boolean for '{field}': {value!r} (use true/false).")
    raise typer.Exit(1)


def _parse_arg_spec(spec: str) -> dict:
    """Parse one ``--arg 'key=...,label=...'`` spec into a session-arg dict.

    Raises typer.Exit(1) with a clear message on malformed input.
    """
    fields: dict[str, str] = {}
    for raw_pair in spec.split(","):
        pair = raw_pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            console.print(
                f"[red]✗[/red] Bad --arg segment {pair!r}: expected field=value."
            )
            raise typer.Exit(1)
        name, value = pair.split("=", 1)
        name = name.strip().lower()
        if name not in _ARG_FIELDS:
            valid = ", ".join(sorted(set(_ARG_FIELDS)))
            console.print(f"[red]✗[/red] Unknown --arg field {name!r}. Valid: {valid}.")
            raise typer.Exit(1)
        fields[_ARG_FIELDS[name]] = value.strip()

    key = fields.get("key")
    if not key:
        console.print(f"[red]✗[/red] --arg {spec!r} is missing required 'key'.")
        raise typer.Exit(1)

    arg_type = fields.get("type", "text").lower()
    if arg_type not in _VALID_ARG_TYPES:
        console.print(
            f"[red]✗[/red] Invalid type {arg_type!r} for '{key}'. "
            f"Valid: {', '.join(sorted(_VALID_ARG_TYPES))}."
        )
        raise typer.Exit(1)

    out: dict = {
        "key": key,
        "label": fields.get("label") or key,
        "type": arg_type,
        "required": _parse_bool(fields["required"], field=f"{key}.required")
        if "required" in fields
        else False,
    }
    if "default" in fields:
        out["default"] = fields["default"]
    if "help_text" in fields:
        out["help_text"] = fields["help_text"]
    if arg_type == "select":
        options = [o.strip() for o in fields.get("options", "").split("|") if o.strip()]
        if not options:
            console.print(
                f"[red]✗[/red] select arg '{key}' needs options=a|b|c."
            )
            raise typer.Exit(1)
        out["options"] = options
    return out


def _parse_args(specs: list[str]) -> list[dict]:
    parsed = [_parse_arg_spec(s) for s in specs]
    keys = [a["key"] for a in parsed]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        console.print(f"[red]✗[/red] Duplicate session arg key(s): {', '.join(sorted(dupes))}.")
        raise typer.Exit(1)
    return parsed


def _print_session_args(session_args: list[dict]) -> None:
    if not session_args:
        console.print("[dim]No session arguments.[/dim]")
        return
    table = Table(title="Session arguments", show_header=True)
    table.add_column("Key", style="cyan")
    table.add_column("Label", style="white")
    table.add_column("Type", style="magenta")
    table.add_column("Required", style="dim")
    table.add_column("Default", style="green")
    table.add_column("Options", style="yellow")
    for a in session_args:
        table.add_row(
            a.get("key", ""),
            a.get("label", ""),
            a.get("type", "text"),
            "yes" if a.get("required") else "no",
            "" if a.get("default") in (None, "") else str(a.get("default")),
            " | ".join(a.get("options") or []),
        )
    console.print(table)


def _handle(e: RuntmError) -> None:
    console.print(f"[red]✗[/red] {e.message}")
    if e.recovery_hint:
        console.print(f"    {e.recovery_hint}")
    raise typer.Exit(1)


def template_list_command(org_id: str | None = None) -> None:
    """List org templates."""
    from runtm_cli.telemetry import command_span

    with command_span("template_list"):
        _require_auth()
        client = APIClient()
        try:
            templates = client.list_org_templates(org_id=org_id)
        except RuntmError as e:
            _handle(e)

        if not templates:
            console.print("[dim]No templates found for this organization.[/dim]")
            return

        table = Table(title="Org templates", show_header=True)
        table.add_column("ID", style="dim")
        table.add_column("Name", style="cyan")
        table.add_column("Display name", style="white")
        table.add_column("Args", style="yellow")
        table.add_column("Build", style="magenta")
        for t in templates:
            table.add_row(
                str(t.get("id", "")),
                str(t.get("name", "")),
                str(t.get("display_name", "")),
                str(len(t.get("session_args") or [])),
                str(t.get("build_status") or ""),
            )
        console.print(table)


def template_show_command(template_id: str, org_id: str | None = None) -> None:
    """Show a single template's details and session arguments."""
    from runtm_cli.telemetry import command_span

    with command_span("template_show"):
        _require_auth()
        client = APIClient()
        try:
            t = client.get_org_template(template_id, org_id=org_id)
        except RuntmError as e:
            _handle(e)

        console.print(f"[cyan]{t.get('display_name')}[/cyan]  [dim]({t.get('name')})[/dim]")
        console.print(f"  ID:    {t.get('id')}")
        if t.get("github_repo"):
            console.print(f"  Repo:  {t.get('github_repo')}@{t.get('github_branch')}")
        console.print(f"  Tier:  {t.get('tier')}")
        console.print(f"  Build: {t.get('build_status')}")
        console.print()
        _print_session_args(t.get("session_args") or [])


def template_create_command(
    display_name: str,
    *,
    repo: str | None = None,
    branch: str = "main",
    tier: str = "basic",
    agents: list[str] | None = None,
    description: str | None = None,
    name: str | None = None,
    arg_specs: list[str] | None = None,
    org_id: str | None = None,
) -> None:
    """Create a template, optionally with session arguments in one call."""
    from runtm_cli.telemetry import command_span

    with command_span("template_create"):
        _require_auth()
        session_args = _parse_args(arg_specs or [])

        payload: dict = {"display_name": display_name, "tier": tier}
        if repo:
            payload["github_repo"] = repo
            payload["github_branch"] = branch
        if description:
            payload["description"] = description
        if name:
            payload["name"] = name
        if agents:
            payload["agents"] = agents
        if session_args:
            payload["session_args"] = session_args

        client = APIClient()
        try:
            t = client.create_org_template(payload, org_id=org_id)
        except RuntmError as e:
            _handle(e)

        console.print(
            f"[green]✓[/green] Created template [cyan]{t.get('display_name')}[/cyan] "
            f"[dim]({t.get('id')})[/dim]"
        )
        console.print()
        _print_session_args(t.get("session_args") or [])


def template_edit_command(
    template_id: str,
    *,
    arg_specs: list[str] | None = None,
    clear_args: bool = False,
    display_name: str | None = None,
    org_id: str | None = None,
) -> None:
    """Edit a template's session arguments (and/or display name).

    Session args are replaced wholesale: ``--arg`` sets the complete list,
    ``--clear-args`` removes them all. Use ``runtm template show`` to view the
    current set first.
    """
    from runtm_cli.telemetry import command_span

    with command_span("template_edit"):
        _require_auth()

        if arg_specs and clear_args:
            console.print("[red]✗[/red] Pass either --arg or --clear-args, not both.")
            raise typer.Exit(1)

        payload: dict = {}
        if clear_args:
            payload["session_args"] = []
        elif arg_specs:
            payload["session_args"] = _parse_args(arg_specs)
        if display_name is not None:
            payload["display_name"] = display_name

        if not payload:
            console.print(
                "[yellow]Nothing to update.[/yellow] Pass --arg, --clear-args, "
                "or --display-name."
            )
            raise typer.Exit(1)

        client = APIClient()
        try:
            t = client.update_org_template(template_id, payload, org_id=org_id)
        except RuntmError as e:
            _handle(e)

        console.print(f"[green]✓[/green] Updated template [dim]{t.get('id')}[/dim]")
        console.print()
        _print_session_args(t.get("session_args") or [])
