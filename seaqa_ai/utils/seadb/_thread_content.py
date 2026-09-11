from dataclasses import dataclass
from enum import Enum


class ContentPriority(Enum):
    BODY_FIRST = 'body_first' # Search snippets: the thread body is the primary signal.
    LATEST_MESSAGE_FIRST = 'latest_message_first' # Agent reading a record: the newest reply matters most.


@dataclass(frozen=True)
class ContentBudget:
    """How a thread's body and messages share one character budget."""
    max_chars: int
    priority: ContentPriority


@dataclass
class ThreadContent:
    """A thread's content after being fitted into a ContentBudget."""
    body: str
    latest_message: dict | None
    history_messages: list  # ascending by time, excluding latest_message
    has_omitted_messages: bool
    # Thread-level text stored once rather than duplicated per message, e.g. a
    # forward's original message. Fitted into the budget like the other parts.
    extras: dict | None = None


def fit_thread_content_to_budget(*, body, messages, budget, extras=None):
    """Fit one thread's body and messages into the shared budget.

    The budget is spent as a greedy waterfall, in decreasing order of
    usefulness for an LLM prompt:

      1. The piece favoured by ``budget.priority`` — the latest message
         for ``LATEST_MESSAGE_FIRST``, the body for ``BODY_FIRST``.
      2. The other one of body / latest message.
      3. The first message, kept as the thread's origin context.
      4. Middle messages, newest first, so recent history survives.

    Each piece is kept whole while budget remains; the first piece that
    overflows is truncated with an ellipsis and spends the rest of the
    budget. Messages reached after the budget is exhausted are omitted
    entirely (flagged via ``has_omitted_messages`` so the caller can
    append a "messages omitted" notice) instead of being truncated to
    an empty string.

    ``messages`` must be ascending by time (guaranteed by the query layer),
    so the latest message is simply the last one.

    ``extras`` is thread-level text kept once per thread (a forward's original
    message). It spends the budget right after the piece favoured by
    ``budget.priority``: a forward's original is usually the reason the mail
    exists, so it should not lose to ordinary history messages.
    """
    messages = [message for message in (messages or []) if message]
    latest_message = messages[-1] if messages else None
    first_message = messages[0] if messages else None
    middle_messages_newest_first = list(reversed(messages[1:-1]))

    remaining_chars = budget.max_chars
    has_omitted_messages = False
    fitted_content_by_message = {}

    def fit_text_within_remaining_budget(content):
        nonlocal remaining_chars
        content = content or ''
        if len(content) <= remaining_chars:
            remaining_chars -= len(content)
            return content, False
        truncated = _truncate_with_ellipsis(content, remaining_chars)
        remaining_chars = 0
        return truncated, True

    def fit_message_within_remaining_budget(message):
        nonlocal has_omitted_messages
        if message is None:
            return
        content = message.get('content') or ''
        if content and remaining_chars <= 0:
            has_omitted_messages = True
            return
        fitted, was_truncated = fit_text_within_remaining_budget(content)
        fitted_content_by_message[id(message)] = fitted
        has_omitted_messages = has_omitted_messages or was_truncated

    # Allocation happens exactly in the priority order documented above.
    if budget.priority is ContentPriority.LATEST_MESSAGE_FIRST:
        fit_message_within_remaining_budget(latest_message)
    fitted_body, _ = fit_text_within_remaining_budget(body)
    if budget.priority is ContentPriority.BODY_FIRST:
        fit_message_within_remaining_budget(latest_message)
    if extras:
        fitted_extras, _ = fit_text_within_remaining_budget(extras.get('text'))
        extras = {**extras, 'text': fitted_extras}
    if first_message is not latest_message:
        fit_message_within_remaining_budget(first_message)
    for message in middle_messages_newest_first:
        fit_message_within_remaining_budget(message)

    def message_with_fitted_content(message):
        if message is None or id(message) not in fitted_content_by_message:
            return None
        fitted = fitted_content_by_message[id(message)]
        if fitted == (message.get('content') or ''):
            return message
        return {**message, 'content': fitted}

    return ThreadContent(
        body=fitted_body,
        latest_message=message_with_fitted_content(latest_message),
        history_messages=[
            fitted for message in messages[:-1]
            if (fitted := message_with_fitted_content(message)) is not None
        ],
        has_omitted_messages=has_omitted_messages,
        extras=extras,
    )


def _truncate_with_ellipsis(content, max_chars):
    if max_chars <= 0:
        return ''
    if len(content) <= max_chars:
        return content
    if max_chars <= 3:
        return '.' * max_chars
    return content[:max_chars - 3] + '...'
