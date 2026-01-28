import logging
from seaqa_ai.config import ENABLE_TEST_TOOL
from seaqa_ai.agent.agent_manager import AgentManager
from seaqa_ai.agent.tools import TestTool
from seaqa_ai.agent.tools.content_generators import GenerateMarkdown
from seaqa_ai.agent.tools.search_tools import DocumentsSearch, IssuesSearch
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP

logger = logging.getLogger(__name__)

class ChatManager:
    def __init__(self, app):
        self.app = app
        self.agent_manager = AgentManager(app)
        self.search_tools = (
            DocumentsSearch(app),
            IssuesSearch(app),
        )
        self.content_generators = (
            GenerateMarkdown(app),
        )
        self.test_tool = TestTool(app)
    
    def _register_tools(self, tool_executor, context):
        for tool in self.search_tools:
            tool.register(tool_executor, context=context)
        for tool in self.content_generators:
            tool.register(tool_executor)
        if ENABLE_TEST_TOOL:
            self.test_tool.register(tool_executor)


    def get_ai_reply(self, resolve_type, message, attachments, context, model, stream=False):
        if resolve_type == AGENT_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            tool_executor = SmolAgentsToolExecutor()
        elif resolve_type == ASK_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            tool_executor = FunctionCallingToolExecutor()
        else:
            raise  ValueError(f"Invalid resolve type")

        self._register_tools(tool_executor, context=context)
        
        try:
            if stream:
                return self.agent_manager.execute_with_stream(resolve_type, model, tool_executor, message, attachments, context=context)
            return self.agent_manager.execute(resolve_type, model, tool_executor, message, attachments, context=context)
        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"
