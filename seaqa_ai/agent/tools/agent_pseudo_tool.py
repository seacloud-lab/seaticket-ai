from seaqa_ai.utils.tools.pseudo_tool import BasicPseudoTool


class AgentPseudoTool(BasicPseudoTool):
    EMPTY_SEARCH_MESSAGE = (
        'No results found for this query. '
        'Try a different query or use another search tool.'
    )

    SEARCH_LIMIT_MESSAGE = (
        'The maximum number of search records has been reached. '
        'Please use other non-search tools to continue the analysis, '
        'or call the termination tool to produce a final analysis report '
        'based on the information already collected.'
    )

    @classmethod
    def inject_max_step_pseudo_outputs(cls, memory: list, terminator_tool: str) -> None:
        call_id = ''
        for item in reversed(memory):
            if item.get('type') == 'function_call':
                call_id = item.get('call_id', '')
                break
        memory.append({
            'type': 'function_call_output',
            'call_id': call_id,
            'output': (
                'The reasoning loop has reached its maximum steps. '
                f'You must call the termination tool "{terminator_tool}" now. '
                'Do not call any other tool.'
            ),
        })
