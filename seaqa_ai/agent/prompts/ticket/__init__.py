from seaqa_ai.agent.utils.constants import DataEventType
from seaqa_ai.agent.prompts.ticket import time_trigger

# Maps every DataEventType relevant to ticket to its prompt module.
PROMPTS = {
    DataEventType.TICKET_DUE_SOON: time_trigger,
}
