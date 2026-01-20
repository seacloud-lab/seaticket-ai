import smolagents
import logging
from seaqa_ai.utils import parse_json, object_to_json_str, filter_tool_calls_content

logger = logging.getLogger(__name__)

class ToolCallingAgent(smolagents.ToolCallingAgent):

    def __init__(self, db_session_class, session_uuid, message_id, tool_executor, model, prompt_templates = None, planning_interval = None, stream_outputs = False, **kwargs):
        self.db_session_class = db_session_class
        self.session_uuid = session_uuid
        self.message_id = message_id
        self.tool_executor = tool_executor

        super().__init__(tool_executor.tools, model, prompt_templates, planning_interval, stream_outputs, None, **kwargs)

    def process_single_tool_call(self, tool_call: smolagents.ToolCall) -> smolagents.ToolOutput:
        tool_name = tool_call.name
        tool_arguments = tool_call.arguments or {}
        self.logger.log(
            smolagents.Panel(smolagents.Text(f"Calling tool: '{tool_name}' with arguments: {tool_arguments}")),
            level=smolagents.LogLevel.INFO,
        )
        try:
            tool_call_result = self.execute_tool_call(tool_name, tool_arguments)
        except Exception as e:
            self.tool_executor.thought_process.add_tool_call(tool_name, tool_arguments)
            logger.exception(f'Execute tool call failure: {e}')
            raise
        tool_call_result_type = type(tool_call_result)
        if tool_call_result_type in [smolagents.AgentImage, smolagents.AgentAudio]:
            if tool_call_result_type == smolagents.AgentImage:
                observation_name = "image.png"
            elif tool_call_result_type == smolagents.AgentAudio:
                observation_name = "audio.mp3"
            # TODO: tool_call_result naming could allow for different names of same type
            self.state[observation_name] = tool_call_result
            observation = f"Stored '{observation_name}' in memory."
            self.tool_executor.thought_process.add_tool_call(tool_name, tool_arguments, filter_tool_calls_content(tool_name, observation))
        else:
            if isinstance(tool_call_result, smolagents.AgentText):
                tool_call_result = parse_json(tool_call_result)
            self.tool_executor.thought_process.add_tool_call(tool_name, tool_arguments, filter_tool_calls_content(tool_name, tool_call_result))
            observation = str(object_to_json_str(tool_call_result)).strip()

        self.logger.log(
            f"Observations: {observation.replace('[', '|')}",  # escape potential rich-tag-like components
            level=smolagents.LogLevel.INFO,
        )
        is_final_answer = tool_name == "final_answer"

        return smolagents.ToolOutput(
            id=tool_call.id,
            output=tool_call_result,
            is_final_answer=is_final_answer,
            observation=observation,
            tool_call=tool_call
        )

    def process_tool_calls(
        self, chat_message: smolagents.ChatMessage, memory_step: smolagents.ActionStep
    ) -> smolagents.Generator[smolagents.ToolCall | smolagents.ToolOutput]:
        """Process tool calls (single thread) from the model output and update agent memory.

        Args:
            chat_message (`ChatMessage`): Chat message containing tool calls from the model.
            memory_step (`ActionStep)`: Memory ActionStep to update with results.

        Yields:
            `ToolCall | ToolOutput`: The tool call or tool output.
        """
        parallel_calls: dict[str, smolagents.ToolCall] = {}
        assert chat_message.tool_calls is not None
        for chat_tool_call in chat_message.tool_calls:
            tool_call = smolagents.ToolCall(
                name=chat_tool_call.function.name, arguments=chat_tool_call.function.arguments, id=chat_tool_call.id
            )
            yield tool_call
            parallel_calls[tool_call.id] = tool_call

        # Helper function to process a single tool call

        # Process tool calls in parallel
        outputs = {}
        self.tool_executor.thought_process.add_tool_calls_group()
        for tool_call in parallel_calls.values():
            tool_output = self.process_single_tool_call(tool_call)
            outputs[tool_output.id] = tool_output
            yield tool_output

        memory_step.tool_calls = [parallel_calls[k] for k in sorted(parallel_calls.keys())]
        memory_step.observations = memory_step.observations or ""
        for tool_output in [outputs[k] for k in sorted(outputs.keys())]:
            memory_step.observations += tool_output.observation + "\n"

        memory_step.observations = (
            memory_step.observations.rstrip("\n") if memory_step.observations else memory_step.observations
        )
