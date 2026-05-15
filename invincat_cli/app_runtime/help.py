"""Help content builder for the interactive Textual app."""

from __future__ import annotations

from textual.content import Content
from textual.style import Style as TStyle

from invincat_cli.commands.registry import COMMANDS
from invincat_cli.config import newline_shortcut
from invincat_cli.core.version import DOCS_URL
from invincat_cli.i18n import t


def build_help_content() -> Content:
    """Build the localized `/help` message content."""
    command_names = [entry.name for entry in COMMANDS]
    help_body = (
        f"{t('help.title')}: {', '.join(command_names)}\n"
        "/model [1|2] [--model-params JSON] [--default]\n\n"
        f"{t('help.interactive_features')}:\n"
        f"  Enter           {t('help.submit')}\n"
        f"  {newline_shortcut():<15} {t('help.insert_newline')}\n"
        f"  Ctrl+X          {t('help.open_editor')}\n"
        f"  Shift+Tab       {t('help.toggle_auto_approve')}\n"
        f"  @filename       {t('help.autocomplete_files')}\n"
        f"  /command        {t('help.slash_commands')}\n"
        f"  !               {t('help.shell_commands')}\n\n"
        f"{t('help.docs')}: "
    )
    return Content.assemble(
        (help_body, "dim italic"),
        (DOCS_URL, TStyle(dim=True, italic=True, link=DOCS_URL)),
    )
