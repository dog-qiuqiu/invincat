"""Model label widget used by the status bar."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len, get_character_cell_size
from textual.content import Content
from textual.reactive import reactive
from textual.widget import Widget

from invincat_cli import theme

if TYPE_CHECKING:
    from textual.app import RenderResult
    from textual.geometry import Size


def _take_right_cells(text: str, max_cells: int) -> str:
    """Return the right-most substring whose rendered width <= max_cells."""
    if max_cells <= 0 or not text:
        return ""
    kept: list[str] = []
    used = 0
    for ch in reversed(text):
        ch_cells = get_character_cell_size(ch)
        if used + ch_cells > max_cells:
            break
        kept.append(ch)
        used += ch_cells
    return "".join(reversed(kept))


class ModelLabel(Widget):
    """A right-aligned model label with width-aware left truncation."""

    provider: reactive[str] = reactive("", layout=True)
    model: reactive[str] = reactive("", layout=True)
    prefix: reactive[str] = reactive("", layout=True)

    def get_content_width(self, container: Size, viewport: Size) -> int:  # noqa: ARG002
        """Return the intrinsic width so `width: auto` works."""
        if not self.model:
            return 0
        full = f"{self.prefix}{self.model}"
        return cell_len(full)

    def render(self) -> RenderResult:
        """Render the model label with width-aware truncation."""
        width = self.content_size.width
        if not self.model or width <= 0:
            return ""
        full = f"{self.prefix}{self.model}"
        colors = theme.get_theme_colors(self)
        if cell_len(full) <= width:
            if self.prefix:
                return Content.assemble(
                    (self.prefix, colors.primary),
                    (self.model, ""),
                )
            return Content(full)
        if width > 1:
            text = "\u2026" + _take_right_cells(full, width - 1)
            if self.prefix and text.startswith(self.prefix):
                return Content.assemble(
                    (self.prefix, colors.primary),
                    (text[len(self.prefix) :], ""),
                )
            return Content(text)
        return Content("\u2026")
