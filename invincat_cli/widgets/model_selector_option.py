"""Model option row widget for the model selector."""

from __future__ import annotations

from textual.content import Content
from textual.events import Click
from textual.message import Message
from textual.widgets import Static


class ModelOption(Static):
    """A clickable model option in the selector."""

    def __init__(
        self,
        label: str | Content,
        model_spec: str,
        provider: str,
        index: int,
        *,
        has_creds: bool | None = True,
        classes: str = "",
    ) -> None:
        """Initialize a model option.

        Args:
            label: Display content — a `Content` object (preferred) or a
                plain string that `Static` will parse as markup.
            model_spec: The model specification (provider:model format).
            provider: The provider name.
            index: The index of this option in the filtered list.
            has_creds: Whether the provider has valid credentials. True if
                confirmed, False if missing, None if unknown.
            classes: CSS classes for styling.
        """
        super().__init__(label, classes=classes)
        self.model_spec = model_spec
        self.provider = provider
        self.index = index
        self.has_creds = has_creds

    class Clicked(Message):
        """Message sent when a model option is clicked."""

        def __init__(self, model_spec: str, provider: str, index: int) -> None:
            """Initialize the Clicked message.

            Args:
                model_spec: The model specification.
                provider: The provider name.
                index: The index of the clicked option.
            """
            super().__init__()
            self.model_spec = model_spec
            self.provider = provider
            self.index = index

    def on_click(self, event: Click) -> None:
        """Handle click on this option.

        Args:
            event: The click event.
        """
        event.stop()
        self.post_message(self.Clicked(self.model_spec, self.provider, self.index))
