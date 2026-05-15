"""Turn cleanup and token reporting helpers for the textual adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from invincat_cli.core.session_stats import SessionStats
from invincat_cli.textual_adapter import turn_cleanup as _turn_cleanup
from invincat_cli.textual_adapter.ui_adapter import TextualUIAdapter

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

async def _handle_interrupt_cleanup(
    *,
    adapter: TextualUIAdapter,
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    config: RunnableConfig,
    pending_text_by_namespace: dict[tuple, str],
    captured_input_tokens: int,
    captured_output_tokens: int,
    turn_stats: SessionStats,
    start_time: float,
) -> None:
    """Shared cleanup for CancelledError and KeyboardInterrupt."""
    from invincat_cli import textual_adapter as adapter_mod

    await _turn_cleanup.handle_interrupt_cleanup(
        adapter=adapter,
        agent=agent,
        config=config,
        pending_text_by_namespace=pending_text_by_namespace,
        captured_input_tokens=captured_input_tokens,
        captured_output_tokens=captured_output_tokens,
        turn_stats=turn_stats,
        start_time=start_time,
        build_interrupted_ai_message_func=adapter_mod._build_interrupted_ai_message,
        report_and_persist_tokens_func=adapter_mod._report_and_persist_tokens,
    )


async def _persist_context_tokens(
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    config: RunnableConfig,
    tokens: int,
) -> None:
    """Best-effort persist of the context token count into graph state."""
    await _turn_cleanup.persist_context_tokens(agent, config, tokens)


async def _report_and_persist_tokens(
    adapter: TextualUIAdapter,
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    config: RunnableConfig,
    captured_input_tokens: int,
    captured_output_tokens: int,
    *,
    shield: bool = False,
    approximate: bool = False,
) -> None:
    """Update the token display and best-effort persist to graph state."""
    from invincat_cli import textual_adapter as adapter_mod

    await _turn_cleanup.report_and_persist_tokens(
        adapter,
        agent,
        config,
        captured_input_tokens,
        captured_output_tokens,
        shield=shield,
        approximate=approximate,
        persist_context_tokens_func=adapter_mod._persist_context_tokens,
    )


async def _flush_assistant_text_ns(
    adapter: TextualUIAdapter,
    text: str,
    ns_key: tuple,
    assistant_message_by_namespace: dict[tuple, Any],
) -> None:
    """Flush accumulated assistant text for a specific namespace."""
    from invincat_cli import textual_adapter as adapter_mod

    await _turn_cleanup.flush_assistant_text_ns(
        adapter,
        text,
        ns_key,
        assistant_message_by_namespace,
        assistant_message_cls=adapter_mod.AssistantMessage,
    )
