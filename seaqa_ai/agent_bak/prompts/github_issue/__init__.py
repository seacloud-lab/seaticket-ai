from seaqa_ai.agent.utils.constants import DataEventType
from seaqa_ai.agent.prompts.github_issue import (
    record_created,
    new_comment,
)

# Maps every DataEventType relevant to github_issue to its prompt module.
PROMPTS = {
    DataEventType.GITHUB_ISSUE_ADDED: record_created,
    DataEventType.GITHUB_ISSUE_COMMENT_ADDED: new_comment,
}
