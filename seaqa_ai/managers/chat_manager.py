import logging
from seaqa_ai.config import ENABLE_TEST_TOOL
from seaqa_ai.agent.tools import TestTool
from seaqa_ai.agent.tools.content_generators import GenerateMarkdown
from seaqa_ai.agent.tools.search_tools import DocumentsSearch, IssuesSearch
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP

logger = logging.getLogger(__name__)

class ChatManager:
    def __init__(self, app):
        self.app = app
        self.search_tools = (
            DocumentsSearch(app),
            IssuesSearch(app),
        )
        self.content_generators = (
            GenerateMarkdown(app),
        )
        self.test_tool = TestTool(app)

    def get_ai_reply(self, resolve_type, message, attachments, context, model):
        if resolve_type == AGENT_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            return self._gen_ans_agent(message, attachments, context, model)
        elif resolve_type == ASK_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            return self._gen_ans_ask(message, attachments, context, model)
        else:
            return f"Invalid resolve type"
    
    def _register_tools(self, tool_executor, context):
        for tool in self.search_tools:
            tool.register(tool_executor, context=context)
        for tool in self.content_generators:
            tool.register(tool_executor)
        if ENABLE_TEST_TOOL:
            self.test_tool.register(tool_executor)

    def _gen_ans_ask(self, message, attachments, context, model):
        """
        Generate an answer based on the question and references.
        """
        tool_executor = FunctionCallingToolExecutor()
        self._register_tools(tool_executor, context=context)

        try:
            return self.app.agent_manager.execute(
                ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value,
                model,
                tool_executor,
                message,
                attachments,
                context=context,
            )

        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"

    def _gen_ans_agent(self, message, attachments, context, model):
        tool_executor = SmolAgentsToolExecutor()
        self._register_tools(tool_executor, context=context)

        return self.app.agent_manager.execute(AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, model, tool_executor, message, attachments, context=context)
