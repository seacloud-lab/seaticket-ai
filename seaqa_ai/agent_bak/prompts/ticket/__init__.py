from seaqa_ai.agent.utils.constants import DataEventType
from seaqa_ai.agent.prompts.ticket import due_soon, over_due, linked_record_activity, task_finished

# Maps every DataEventType relevant to ticket to its prompt module.
PROMPTS = {
    DataEventType.TICKET_DUE_SOON: due_soon,
    DataEventType.TICKET_OVER_DUE: over_due,
    DataEventType.TICKET_LINKED_RECORD_ACTIVITY: linked_record_activity,
    DataEventType.TICKET_TASK_FINISHED: task_finished,
}
