from seaqa_ai.agent.utils.constants import DataEventType
from seaqa_ai.agent.prompts.email import (
    record_created,
    new_reply,
)

# Maps every DataEventType relevant to email to its prompt module.
PROMPTS = {
    DataEventType.EMAIL_THREAD_ADDED: record_created,
    DataEventType.EMAIL_MESSAGE_ADDED: new_reply,
}
