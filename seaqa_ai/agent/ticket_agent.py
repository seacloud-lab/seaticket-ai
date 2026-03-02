"""
TicketAgent: Agent for processing tickets via LiteLLM function calling.

This agent:
1. Injects ticket context into tool executions
2. Records actions to SeaDB for auditing and user review
3. Handles the special 'pending' status for actions requiring user confirmation
"""
import json
import logging
from seaqa_ai.utils.llm_api import CompletionAPI
from seaqa_ai.agent.tools.agent_tools import FINAL_ANSWER_TOOL_CLASS
from seaqa_ai.utils.tools import SimpleToolExecutor
from litellm import Message, ChatCompletionMessageToolCall

logger = logging.getLogger(__name__)


class TicketAgent:
    """
    Agent specialized for ticket analysis and action suggestions.

    Features:
    - Injects ticket context (ticket_id, ticket_title) into tool calls
    - Records each tool execution as an action in SeaDB
    - Supports 'pending' status for actions requiring user confirmation
    """

    def __init__(
        self,
        tool_executor: SimpleToolExecutor,
        llm_client: CompletionAPI,
        ticket_context: dict,
        action_recorder=None,
        run_id=None,
        tool_metadata=None,
        system_prompt=None,
        max_steps=10,
    ):
        """
        Initialize the TicketAgent.

        Args:
            tool_executor: SimpleToolExecutor with registered tools
            llm_client: CompletionAPI client for LLM calls
            ticket_context: Dict containing ticket_id, ticket_title
            action_recorder: Callable to record actions to SeaDB
            run_id: The current agent run ID
            tool_metadata: Dict mapping tool_name to {action_type, action_status}
            system_prompt: Optional custom system prompt
            max_steps: Maximum reasoning steps
        """
        self.tool_executor = tool_executor
        self.llm_client = llm_client
        self.ticket_context = ticket_context
        self.action_recorder = action_recorder
        self.run_id = run_id
        self.tool_metadata = tool_metadata or {}
        self.system_prompt = system_prompt or ''
        self.max_steps = max_steps

        final_answer_tool = FINAL_ANSWER_TOOL_CLASS(None)
        tool_executor.register(final_answer_tool.execute, final_answer_tool.tool)

        self.tools = tool_executor.get()

    def run(self, task: str) -> str:
        """
        Run the agent reasoning loop on a task.

        Calls LLM -> handles tool calls -> feeds results back -> repeats
        until final_answer is called or max_steps is reached.
        """
        messages = self._build_initial_messages(task)

        for step in range(self.max_steps):
            message = self._request_llm_message(messages, step)
            assistant_message = self._build_assistant_message(message)
            messages.append(assistant_message)

            if not message.tool_calls:
                return message.content or ''

            final_result = self._process_tool_calls(message.tool_calls, messages)
            if final_result is not None:
                return final_result

        logger.warning(f'Agent reached max steps ({self.max_steps})')
        return ''

    def _build_initial_messages(self, task: str) -> list[dict]:
        return [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': task},
        ]

    def _request_llm_message(self, messages: list[dict], step: int) -> Message:
        try:
            response = self.llm_client.completion(
                messages=messages,
                tools=self.tools,
                tool_choice='auto',
            )
            return response.choices[0].message
        except Exception as e:
            logger.exception(f'LLM call failed at step {step}: {e}')
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=None, 
                    arguments={}, 
                    result=str(e),
                    action_type='error', 
                    action_status='failed',
                    content=f'LLM error: {e}'
                )
            raise

    def _build_assistant_message(self, message: Message) -> dict:
        msg_dict = {'role': 'assistant', 'content': message.content or ''}
        if message.tool_calls:
            msg_dict['tool_calls'] = [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {
                        'name': tc.function.name,
                        'arguments': tc.function.arguments,
                    }
                }
                for tc in message.tool_calls
            ]
        return msg_dict

    def _process_tool_calls(self, tool_calls: list[ChatCompletionMessageToolCall], messages: list[dict]) -> str | None:
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            arguments = self._parse_tool_arguments(tool_call.function.arguments)

            logger.info(f"Calling tool: '{tool_name}' with arguments: {arguments}")
            observation = self._execute_tool_and_observe(tool_name, arguments)
            logger.info(f"Observations: {observation}")

            is_final_answer = tool_name == 'final_answer'
            self._record_tool_action(tool_name, arguments, observation, is_final_answer)

            if is_final_answer:
                return observation

            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'content': observation,
            })

        return None

    def _parse_tool_arguments(self, raw_args) -> dict:
        try:
            return json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except (json.JSONDecodeError, TypeError):
            return {}

    def _execute_tool_call(self, tool_name: str, arguments: dict):
        """Execute a tool call by name."""
        if tool_name == 'final_answer':
            return arguments.get('answer', '') or arguments.get('summary', '') or ''
        return self.tool_executor.execute(tool_name, **arguments)

    def _execute_tool_and_observe(self, tool_name: str, arguments: dict) -> str:
        try:
            result = self._execute_tool_call(tool_name, arguments)
        except Exception as e:
            logger.exception(f'Execute tool call failure: {e}')
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=tool_name, 
                    arguments=arguments,
                    result=str(e), 
                    action_type='error', 
                    action_status='failed'
                )
            raise
        return str(result).strip() if result is not None else ''

    def _record_tool_action(self, tool_name: str, arguments: dict, observation: str, is_final_answer: bool):
        if not (self.action_recorder and self.run_id):
            return

        if is_final_answer:
            summary = arguments.get('summary') or arguments.get('answer') or observation
            self._record_action(
                tool_name='final_answer', 
                arguments=arguments, 
                result=None,
                action_type='summary', 
                action_status='completed', 
                content=summary
            )
            return

        metadata = self.tool_metadata.get(tool_name, {})
        action_type = metadata.get('action_type', 'tool_call')
        action_status = metadata.get('action_status', 'completed')
        suggestion_text = metadata.get('suggestion_text', '') if action_type == 'suggestion' else ''
        content = (
            arguments.get('analysis') or
            arguments.get('message') or
            arguments.get('comment') or
            arguments.get('content') or
            ''
        )
        self._record_action(
            tool_name=tool_name, 
            arguments=arguments, 
            result=observation,
            action_type=action_type, 
            action_status=action_status,
            content=content, 
            suggestion_text=suggestion_text
        )

    def _record_action(self, tool_name, arguments, result, action_type, action_status, content=None, suggestion_text=None):
        """Record an action to SeaDB via the action_recorder callback."""
        if not self.action_recorder:
            return
        try:
            self.action_recorder(
                run_id=self.run_id,
                ticket_id=self.ticket_context.get('ticket_id'),
                ticket_title=self.ticket_context.get('ticket_title'),
                action_type=action_type,
                tool_name=tool_name,
                content=content or '',
                result=result,
                action_status=action_status,
                suggestion_text=suggestion_text or ''
            )
        except Exception as e:
            logger.exception(f'Failed to record action: {e}')
