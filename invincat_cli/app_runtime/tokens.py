"""Token usage presentation helpers for the Textual app."""

from __future__ import annotations

from invincat_cli.core.session_stats import format_token_count
from invincat_cli.i18n import t


def build_tokens_message(
    *,
    context_tokens: int,
    model_name: str,
    context_limit: int | None,
    conversation_tokens: int | None = None,
) -> str:
    """Build the `/tokens` command response text."""
    if context_tokens <= 0:
        parts: list[str] = [t("tokens.no_usage_yet")]
        if context_limit is not None:
            parts.append(
                t("tokens.context_window").format(
                    limit=format_token_count(context_limit)
                )
            )
        if model_name:
            parts.append(model_name)
        return " · ".join(parts)

    formatted = format_token_count(context_tokens)
    if context_limit is not None:
        limit_str = format_token_count(context_limit)
        pct = context_tokens / context_limit * 100
        usage = t("tokens.usage_with_limit").format(
            used=formatted,
            limit=limit_str,
            pct=f"{pct:.0f}",
        )
    else:
        usage = t("tokens.usage_simple").format(used=formatted)

    msg = f"{usage} · {model_name}" if model_name else usage
    if conversation_tokens is None:
        return msg

    overhead = max(0, context_tokens - conversation_tokens)
    overhead_str = format_token_count(overhead)
    conversation_str = format_token_count(conversation_tokens)
    overhead_unit = " tokens" if overhead < 1000 else ""
    conversation_unit = " tokens" if conversation_tokens < 1000 else ""

    return (
        f"{msg}\n"
        f"{t('tokens.system_tools_fixed').format(tokens=f'{overhead_str}{overhead_unit}')}\n"
        f"{t('tokens.conversation').format(tokens=f'{conversation_str}{conversation_unit}')}"
    )
