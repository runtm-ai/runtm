"""CLI commands."""

from runtm_cli.commands.approve import approve_command
from runtm_cli.commands.deploy import deploy_command
from runtm_cli.commands.destroy import destroy_command
from runtm_cli.commands.doctor import doctor_command
from runtm_cli.commands.domain import (
    domain_add_command,
    domain_remove_command,
    domain_status_command,
)
from runtm_cli.commands.fix import fix_command
from runtm_cli.commands.init import init_command
from runtm_cli.commands.list import list_command
from runtm_cli.commands.logs import logs_command
from runtm_cli.commands.run import run_command
from runtm_cli.commands.search import search_command
from runtm_cli.commands.secrets import (
    secrets_get_command,
    secrets_list_command,
    secrets_set_command,
    secrets_unset_command,
)
from runtm_cli.commands.session import session_app
from runtm_cli.commands.status import status_command
from runtm_cli.commands.template import (
    template_create_command,
    template_edit_command,
    template_list_command,
    template_show_command,
)
from runtm_cli.commands.validate import validate_command

__all__ = [
    "approve_command",
    "deploy_command",
    "destroy_command",
    "doctor_command",
    "domain_add_command",
    "domain_remove_command",
    "domain_status_command",
    "fix_command",
    "init_command",
    "list_command",
    "logs_command",
    "run_command",
    "search_command",
    "secrets_get_command",
    "secrets_list_command",
    "secrets_set_command",
    "secrets_unset_command",
    "session_app",
    "status_command",
    "template_create_command",
    "template_edit_command",
    "template_list_command",
    "template_show_command",
    "validate_command",
]
