"""
AgentRunner: generic ReAct-style LLM function-calling loop.

Works for any source type (ticket, github_issue, discourse_topic, email_thread).
The caller injects a record_context dict that identifies the source record being processed:
  {
    'source_type': 'github_issue',
    'source_id': '3_42',          # '{connection_id}_{record_id}' for non-ticket sources
    'source_title': 'Login fails on Safari',
  }

Actions are recorded to SeaDB via the action_recorder callback.
"""
import json
import logging
from seaqa_ai.utils.llm_api import CompletionAPI
from seaqa_ai.agent.tools.common_tools import FinalAnswerTool, ThinkTool
from seaqa_ai.utils.tools import OpenAIToolExecutor
from litellm import Message, ChatCompletionMessageToolCall

logger = logging.getLogger(__name__)


class AgentRunner:
    """
    Generic agent that drives a tool-calling reasoning loop.

    Features:
    - Injects record context (source_type, source_id, source_title) into tool calls
    - Records each tool execution as an action in SeaDB via action_recorder
    - Supports 'pending' status for actions requiring user confirmation
    """

    def __init__(
        self,
        tool_executor: OpenAIToolExecutor,
        llm_client: CompletionAPI,
        record_context: dict,
        ai_stats_context: dict | None = None,
        action_recorder=None,
        run_id=None,
        tool_metadata=None,
        system_prompt=None,
        max_steps=10,
    ):
        """
        Args:
            tool_executor: OpenAIToolExecutor with all tools already registered.
            llm_client: CompletionAPI client used for LLM calls.
            record_context: dict with keys source_type, source_id, source_title.
            ai_stats_context: dict with keys used for AI usage statistics logging
                            (e.g. username, org_id, project_uuid).
            action_recorder: callable(run_id, source_type, source_id, source_title,
                             action_type, tool_name, content, result, action_status,
                             suggestion_text) -> int
            run_id: current agent run _pk in SeaDB.
            tool_metadata: dict mapping tool_name to {action_type, action_status, suggestion_text}.
            system_prompt: system message injected at the start of each reasoning loop.
            max_steps: maximum number of LLM calls before giving up.
        """
        self.tool_executor = tool_executor
        self.llm_client = llm_client
        self.record_context = record_context
        self.ai_stats_context = ai_stats_context or {}
        self.action_recorder = action_recorder
        self.run_id = run_id
        self.tool_metadata = tool_metadata or {}
        self.system_prompt = system_prompt or ''
        self.max_steps = max_steps

        final_answer_tool = FinalAnswerTool()
        final_answer_tool.register(tool_executor)

        think_tool = ThinkTool()
        think_tool.register(tool_executor)
        self.tool_metadata[think_tool.tool.get('function', {}).get('name')] = {
            'action_type': think_tool.action_type,
            'action_status': think_tool.action_status,
        }

    def run(self, task: str) -> str:
        """Drive the reasoning loop until final_answer is called or max_steps reached."""
        messages = self._build_initial_messages(task)

        for step in range(self.max_steps):
            self._force_think_step(messages, step)
            message = self._request_llm_message(messages, step, use_tools=True)
            tool_call = self._validate_single_tool_call(message.tool_calls, step)
            assistant_message = self._build_assistant_message(message, tool_call=tool_call)
            messages.append(assistant_message)

            if not tool_call:
                return message.content or ''

            final_result = self._process_tool_call(tool_call, messages)
            if final_result is not None:
                return final_result

        logger.warning(f'Agent reached max steps ({self.max_steps})')
        final_message = self._request_llm_message(
            messages=messages,
            step=self.max_steps,
            use_tools=False,
        )
        return final_message.content or ''

    def _build_initial_messages(self, task: str) -> list[dict]:
        return [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': task},
        ]

    def _request_llm_message(self, messages: list[dict], step: int, use_tools: bool = True) -> Message:
        try:
            request_kwargs = {
                'messages': messages,
                'context': self.ai_stats_context,
            }
            if use_tools:
                request_kwargs.update({
                    'tools': self._get_regular_tools(),
                    'tool_choice': 'auto',
                })
            response = self.llm_client.completion(**request_kwargs)
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
                    content=f'LLM error: {e}',
                )
            raise

    def _force_think_step(self, messages: list[dict], step: int):
        think_tool = self._get_registered_tool_by_name('think')
        if not think_tool:
            logger.warning('Think tool is not registered; skipping forced think step.')
            return

        think_messages = messages + [{
            'role': 'system',
            'content': (
                'Before taking the next regular action, you must call the think tool exactly once. '
                'Use it to analyze the latest context, verify applicable rules, and plan the next step. '
                'Do not call any other tool in this response.'
            ),
        }]

        try:
            response = self.llm_client.completion(
                messages=think_messages,
                context=self.ai_stats_context,
                tools=[think_tool],
                tool_choice={
                    'type': 'function',
                    'function': {
                        'name': 'think',
                    },
                },
            )
            message = response.choices[0].message
        except Exception as e:
            logger.exception(f'Forced think call failed at step {step}: {e}')
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name='think',
                    arguments={},
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    content=f'Forced think error: {e}',
                )
            raise

        tool_call = self._validate_single_tool_call(message.tool_calls, step)
        assistant_message = self._build_assistant_message(message, tool_call=tool_call)
        messages.append(assistant_message)

        if not tool_call:
            logger.warning(f'Forced think step {step} returned no tool call.')
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name='think',
                    arguments={},
                    result='No think tool call returned.',
                    action_type='error',
                    action_status='failed',
                    content='Forced think step did not produce a think tool call.',
                )
            return

        final_result = self._process_tool_call(tool_call, messages)
        if final_result is not None:
            logger.warning(f'Forced think step {step} unexpectedly returned a final result: {final_result}')

    def _get_regular_tools(self) -> list[dict]:
        """Return tools exposed to the regular step, excluding think."""
        return [
            tool
            for tool in self.tool_executor.get()
            if tool.get('function', {}).get('name') != 'think'
        ]

    def _validate_single_tool_call(
        self,
        tool_calls: list[ChatCompletionMessageToolCall] | None,
        step: int,
    ) -> ChatCompletionMessageToolCall | None:
        """Keep only one tool call per step and warn when model returns more."""
        if not tool_calls:
            return None
        if len(tool_calls) == 1:
            return tool_calls[0]

        tool_names = [tc.function.name for tc in tool_calls]
        logger.warning(
            'Agent step %s returned %s tool calls; keeping only the first one. tool_names=%s',
            step,
            len(tool_calls),
            tool_names,
        )
        return tool_calls[0]

    def _get_registered_tool_by_name(self, tool_name: str) -> dict | None:
        for tool in self.tool_executor.get():
            function = tool.get('function', {})
            if function.get('name') == tool_name:
                return tool
        return None

    def _build_assistant_message(
        self,
        message: Message,
        tool_call: ChatCompletionMessageToolCall | None = None,
    ) -> dict:
        msg_dict = {'role': 'assistant', 'content': message.content or ''}
        if tool_call:
            msg_dict['tool_calls'] = [{
                'id': tool_call.id,
                'type': 'function',
                'function': {
                    'name': tool_call.function.name,
                    'arguments': tool_call.function.arguments,
                }
            }]
        return msg_dict

    def _process_tool_call(
        self,
        tool_call: ChatCompletionMessageToolCall,
        messages: list[dict],
    ) -> str | None:
        self.tool_executor.thought_process.add_tool_calls_group()
        tool_name = tool_call.function.name
        is_final_answer = tool_name == 'final_answer'
        tool_failed = False

        try:
            raw_arguments = tool_call.function.arguments
            parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
            if not isinstance(parsed_arguments, dict):
                raise TypeError('Tool arguments must be a JSON object.')
            arguments = parsed_arguments
        except (json.JSONDecodeError, TypeError) as e:
            tool_failed = True
            arguments = {}
            logger.exception(f'Failed to parse tool arguments for {tool_name!r}: {e}')
            observation = json.dumps({
                'status': 'error',
                'tool_name': tool_name,
                'error_type': type(e).__name__,
                'error_message': str(e),
            }, ensure_ascii=False)
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                )
            logger.debug(f'Observation: {observation}')
            if is_final_answer:
                return observation
            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'content': observation,
            })
            return None

        logger.debug(f"Calling tool: '{tool_name}' with arguments: {arguments}")
        try:
            result = self.tool_executor.execute(tool_call)
            if result is None:
                observation = ''
            elif isinstance(result, str):
                observation = result.strip()
            elif isinstance(result, (dict, list)):
                observation = json.dumps(result, ensure_ascii=False)
            else:
                observation = str(result).strip()
        except Exception as e:
            tool_failed = True
            logger.exception(f'Tool execution failed for {tool_name!r}: {e}')
            observation = json.dumps({
                'status': 'error',
                'tool_name': tool_name,
                'error_type': type(e).__name__,
                'error_message': str(e),
            }, ensure_ascii=False)
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                )
        logger.debug(f'Observation: {observation}')

        if not tool_failed:
            self._record_tool_action(tool_name, arguments, observation, is_final_answer)

        if is_final_answer:
            return observation

        messages.append({
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'content': observation,
        })

    def _record_tool_action(
        self,
        tool_name: str,
        arguments: dict,
        observation: str,
        is_final_answer: bool,
    ):
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
                content=summary,
            )
            return

        metadata = self.tool_metadata.get(tool_name, {})
        action_type = metadata.get('action_type', 'tool_call')
        action_status = metadata.get('action_status', 'completed')
        suggestion_text = metadata.get('suggestion_text', '') if action_type == 'suggestion' else ''
        content = (
            arguments.get('thought') or
            arguments.get('analysis') or
            arguments.get('summary') or
            arguments.get('message') or
            arguments.get('comment') or
            arguments.get('resolution') or
            arguments.get('reason') or
            arguments.get('rationale') or
            arguments.get('content') or
            ''
        )
        if not content and isinstance(arguments, dict) and arguments:
            content = json.dumps(arguments, ensure_ascii=False)
        self._record_action(
            tool_name=tool_name,
            arguments=arguments,
            result=observation,
            action_type=action_type,
            action_status=action_status,
            content=content,
            suggestion_text=suggestion_text,
        )

    def _record_action(
        self,
        tool_name,
        arguments,
        result,
        action_type,
        action_status,
        content=None,
        suggestion_text=None,
    ):
        """Record an action to SeaDB via the action_recorder callback."""
        if not self.action_recorder:
            return
        try:
            self.action_recorder(
                run_id=self.run_id,
                source_type=self.record_context.get('source_type'),
                source_id=self.record_context.get('source_id'),
                source_title=self.record_context.get('source_title'),
                action_type=action_type,
                tool_name=tool_name,
                content=content or '',
                result=result,
                action_status=action_status,
                suggestion_text=suggestion_text or '',
            )
        except Exception as e:
            logger.exception(f'Failed to record action: {e}')
