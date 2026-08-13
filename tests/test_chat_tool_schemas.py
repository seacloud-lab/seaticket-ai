from seaqa_ai.services.chat_service.skills.tool import SkillTool
from seaqa_ai.services.chat_service.tools.content_generators import (
    KnowledgeBaseEntryCreator,
    MarkdownGenerator,
    TicketCreator,
)
from seaqa_ai.utils.tools.search_tools import DocumentsSearch, IssuesSearch, TasksSearch
from seaqa_ai.utils.tools.test_tool import TestTool


def test_strict_chat_tool_schemas_require_all_properties():
    tools = (
        DocumentsSearch(),
        IssuesSearch(),
        TasksSearch(),
        MarkdownGenerator(),
        KnowledgeBaseEntryCreator(),
        TicketCreator(),
        TestTool(),
        SkillTool(),
    )

    for tool in tools:
        definition = tool.tool
        if not definition.get('strict'):
            continue
        parameters = definition['parameters']
        assert set(parameters['required']) == set(parameters['properties'])
