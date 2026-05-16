from seaqa_ai.agent.utils.constants import DataEventType
from seaqa_ai.agent.prompts.discourse import (
    record_created,
    new_comment,
)

# Maps every DataEventType relevant to discourse to its prompt module.
PROMPTS = {
    DataEventType.DISCOURSE_TOPIC_ADDED: record_created,
    DataEventType.DISCOURSE_TOPIC_COMMENT_ADDED: new_comment,
}
