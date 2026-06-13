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

    MAX_STEP_PSEUDO_OUTPUT = (
        'Tool calls are not allowed at this step. '
        'You must now provide a final text answer based on '
        'available information. DO NOT return any tool calls.'
    )

    @classmethod
    def inject_max_step_pseudo_outputs(cls, tool_calls: list, memory: list) -> None:
        memory += list(tool_calls)
        for tool_call in tool_calls:
            memory.append({
                'type': 'function_call_output',
                'call_id': tool_call.get('call_id', ''),
                'output': cls.MAX_STEP_PSEUDO_OUTPUT,
            })
