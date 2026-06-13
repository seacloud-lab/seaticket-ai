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
