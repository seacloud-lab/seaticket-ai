from seaqa_ai.utils.tools.pseudo_tool import BasicPseudoTool


class ChatPseudoTool(BasicPseudoTool):
    EMPTY_SEARCH_MESSAGE = (
        'No results found for this query. '
        'Try a different query or use another search tool.'
    )

    SEARCH_LIMIT_MESSAGE = (
        'The maximum number of search records has been reached. '
        'Please use other non-search tools to complete the request, '
        'or provide your final answer based on the information already collected.'
    )

    MAX_STEP_RETRY_MESSAGE = (
        'You returned tool calls despite tool access being disabled '
        'at the maximum step. You must now provide a final text-only '
        'answer based on available information. DO NOT return any tool calls.'
    )

    @classmethod
    def inject_max_step_retry_message(cls, memory: list) -> None:
        memory.append({'role': 'system', 'content': cls.MAX_STEP_RETRY_MESSAGE})
