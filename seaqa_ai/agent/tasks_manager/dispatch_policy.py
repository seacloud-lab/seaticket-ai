from seaqa_ai.agent.events_manager.constants import (
    EVENT_TYPE_TICKET_DUE_SOON,
    EVENT_TYPE_TICKET_LINKED_RECORD_ACTIVITY,
    EVENT_TYPE_TICKET_OVER_DUE,
    SOURCE_TYPE_TICKET,
)

from seaqa_ai.agent.models import AgentTask


class TaskDispatchPolicy:
    """Keep task dispatch semantics separate from Redis queue mechanics."""

    _IMMEDIATE_TICKET_EVENT_TYPES = frozenset({
        EVENT_TYPE_TICKET_DUE_SOON,
        EVENT_TYPE_TICKET_OVER_DUE,
        EVENT_TYPE_TICKET_LINKED_RECORD_ACTIVITY,
    })

    def should_debounce(self, task: AgentTask) -> bool:
        return (
            task.target_source_type == SOURCE_TYPE_TICKET
            and task.trigger_event.event_type not in self._IMMEDIATE_TICKET_EVENT_TYPES
        )
