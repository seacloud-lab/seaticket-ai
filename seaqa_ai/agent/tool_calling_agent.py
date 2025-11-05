import smolagents
import logging
import json
from seaqa_ai.db.models import ChatToolCalls
from seaqa_ai.config import ENABLE_TOOLS_SHARED_VARS
from seaqa_ai.agent.utils import tool_output, save_tool_call
from seaqa_ai.agent.tool_shared_vars import ToolSharedVars
from seaqa_ai.utils import parse_json

logger = logging.getLogger(__name__)

class ToolCallingAgent(smolagents.ToolCallingAgent):

    def __init__(self, db_session_class, chat_uuid, tools, model, prompt_templates = None, planning_interval = None, stream_outputs = False, **kwargs):
        self.db_session_class = db_session_class
        self.chat_uuid = chat_uuid

        super().__init__(tools, model, prompt_templates, planning_interval, stream_outputs, None, **kwargs)

    def process_single_tool_call(self, tool_call: smolagents.ToolCall) -> smolagents.ToolOutput:
        tool_name = tool_call.name
        tool_arguments = tool_call.arguments or {}
        self.logger.log(
            smolagents.Panel(smolagents.Text(f"Calling tool: '{tool_name}' with arguments: {tool_arguments}")),
            level=smolagents.LogLevel.INFO,
        )

        tool_call_result = self.execute_tool_call(tool_name, tool_arguments)
        tool_call_result_type = type(tool_call_result)
        if tool_call_result_type in [smolagents.AgentImage, smolagents.AgentAudio]:
            if tool_call_result_type == smolagents.AgentImage:
                observation_name = "image.png"
            elif tool_call_result_type == smolagents.AgentAudio:
                observation_name = "audio.mp3"
            # TODO: tool_call_result naming could allow for different names of same type
            self.state[observation_name] = tool_call_result
            observation = f"Stored '{observation_name}' in memory."
        else:
            if isinstance(tool_call_result, smolagents.AgentText):
                tool_call_result = parse_json(tool_call_result)

            to_observation, to_tools_shared_vars, to_db = tool_output(tool_call_result)

            if ENABLE_TOOLS_SHARED_VARS and to_tools_shared_vars:
                ToolSharedVars.update(to_tools_shared_vars)

            save_tool_call(self.db_session_class, self.chat_uuid, tool_call, to_db)
            observation = str(to_observation).strip()
        self.logger.log(
            f"Observations: {observation.replace('[', '|')}",  # escape potential rich-tag-like components
            level=smolagents.LogLevel.INFO,
        )
        is_final_answer = tool_name == "final_answer"

        return smolagents.ToolOutput(
            id=tool_call.id,
            output=to_observation,
            is_final_answer=is_final_answer,
            observation=observation,
            tool_call=tool_call,
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
        for tool_call in parallel_calls.values():
            tool_output = self.process_single_tool_call(tool_call)
            outputs[tool_output.id] = tool_output
            yield tool_output

        memory_step.tool_calls = [parallel_calls[k] for k in sorted(parallel_calls.keys())]
        memory_step.observations = memory_step.observations or ""
        for tool_output in [outputs[k] for k in sorted(outputs.keys())]:
            memory_step.observations += tool_output.observation + "\n"
        
        # save usages infomation to db
        if parallel_calls:
            with self.db_session_class() as db_session:
                record = db_session.query(ChatToolCalls).filter(ChatToolCalls.chat_uuid==self.chat_uuid).first()
                if record:
                    new_tool_calls = record.to_dict()['tool_calls']
                    tool_call_id = list(parallel_calls.values())[0].id
                    if tool_call_id in new_tool_calls:
                        new_tool_calls[tool_call_id]['static'] = {
                            'token_usage': {
                                'input_tokens': memory_step.token_usage.input_tokens,
                                'output_tokens': memory_step.token_usage.output_tokens,
                                'total_tokens': memory_step.token_usage.total_tokens,
                            },
                            'time_usage': memory_step.timing.duration
                        }
                        if memory_step.error:
                            new_tool_calls[tool_call_id]['static']['error'] = {
                                'type': memory_step.error.type,
                                'message': memory_step.error.message
                            }
                        record.tool_calls = json.dumps(new_tool_calls)
                        db_session.commit()

        memory_step.observations = (
            memory_step.observations.rstrip("\n") if memory_step.observations else memory_step.observations
        )
