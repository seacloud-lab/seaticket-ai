"""
TicketAgent: A specialized smolagents-based agent for processing tickets.

This agent extends ToolCallingAgent to:
1. Inject ticket context into tool executions
2. Record actions to SeaDB for auditing and user review
3. Handle the special 'pending' status for actions requiring user confirmation
"""
import logging
import smolagents

from seaqa_ai.utils.tools import SmolAgentsToolExecutor

logger = logging.getLogger(__name__)


class TicketAgent(smolagents.ToolCallingAgent):
    """
    Agent specialized for ticket analysis and action suggestions.
    
    Features:
    - Injects ticket context (ticket_id, ticket_title) into tool calls
    - Records each tool execution as an action in SeaDB
    - Supports 'pending' status for actions requiring user confirmation
    """

    def __init__(
        self,
        tool_executor: SmolAgentsToolExecutor,
        model,
        ticket_context: dict,
        action_recorder=None,
        run_id=None,
        tool_metadata=None,
        system_prompt=None,
        max_steps=10,
        **kwargs
    ):
        """
        Initialize the TicketAgent.
        
        Args:
            tool_executor: SmolAgentsToolExecutor with registered tools
            model: The LLM model to use
            ticket_context: Dict containing ticket_id, ticket_title
            action_recorder: Callable to record actions to SeaDB
            run_id: The current agent run ID
            tool_metadata: Dict mapping tool_name to {action_type, action_status}
            system_prompt: Optional custom system prompt
            max_steps: Maximum reasoning steps
        """
        self.tool_executor = tool_executor
        self.ticket_context = ticket_context
        self.action_recorder = action_recorder
        self.run_id = run_id
        self._custom_system_prompt = system_prompt
        
        # Use provided tool metadata (smolagents loses original class attributes)
        self._tool_metadata = tool_metadata or {}
        
        super().__init__(
            tools=tool_executor.tools,
            model=model,
            max_steps=max_steps,
            **kwargs
        )
        
        # Override system prompt after initialization if provided
        if self._custom_system_prompt:
            self.prompt_templates["system_prompt"] = self._custom_system_prompt

    def execute_tool_call(self, tool_name: str, arguments: dict):
        """
        Execute a tool call.
        
        Note: Ticket context is now set on tool instances via set_ticket_context()
        before registration, so we don't need to inject it here.
        """
        # Handle final_answer specially
        if tool_name == "final_answer":
            return arguments.get("answer", "") or arguments.get("summary", "") or ""
        
        # self.tools is a dict: {tool_name: tool_object}
        tool = self.tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        return tool(**arguments)

    def process_single_tool_call(self, tool_call: smolagents.ToolCall) -> smolagents.ToolOutput:
        """
        Process a single tool call with action recording.
        
        Overrides parent to:
        1. Execute the tool with ticket context
        2. Record the action to SeaDB
        """
        tool_name = tool_call.name
        tool_arguments = tool_call.arguments or {}
        
        self.logger.log(
            smolagents.Panel(smolagents.Text(f"Calling tool: '{tool_name}' with arguments: {tool_arguments}")),
            level=smolagents.LogLevel.INFO,
        )
        
        try:
            tool_call_result = self.execute_tool_call(tool_name, tool_arguments)
        except Exception as e:
            logger.exception(f'Execute tool call failure: {e}')
            # Record error action
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=tool_name,
                    arguments=tool_arguments,
                    result=str(e),
                    action_type='error',
                    action_status='failed'
                )
            raise
        
        # Convert result to string observation
        observation = str(tool_call_result).strip() if tool_call_result else ""
        self.logger.log(
            f"Observations: {observation.replace('[', '|')}",
            level=smolagents.LogLevel.INFO,
        )
        
        # Handle final_answer specially
        is_final_answer = tool_name == "final_answer"

        # Record the action to SeaDB
        if self.action_recorder and self.run_id:
            if is_final_answer:
                # Final answer is recorded as summary
                summary = tool_arguments.get('summary') or tool_arguments.get('answer') or observation
                self._record_action(
                    tool_name='final_answer',
                    arguments=tool_arguments,
                    result=None,
                    action_type='summary',
                    action_status='completed',
                    content=summary
                )
            else:
                # Get metadata for this tool
                metadata = self._tool_metadata.get(tool_name, {})
                action_type = metadata.get('action_type', 'tool_call')
                action_status = metadata.get('action_status', 'completed')
                suggestion_text = metadata.get('suggestion_text', '') if action_type == 'suggestion' else ''
                
                # Extract content from arguments
                content = (
                    tool_arguments.get('analysis') or
                    tool_arguments.get('message') or 
                    tool_arguments.get('comment') or 
                    tool_arguments.get('content') or 
                    ''
                )
                
                self._record_action(
                    tool_name=tool_name,
                    arguments=tool_arguments,
                    result=observation,
                    action_type=action_type,
                    action_status=action_status,
                    content=content,
                    suggestion_text=suggestion_text
                )
        
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
        """
        Process tool calls from the model output and update agent memory.
        
        This override ensures our process_single_tool_call is called (not the
        parent's internal function).
        """
        parallel_calls: dict[str, smolagents.ToolCall] = {}
        assert chat_message.tool_calls is not None
        for chat_tool_call in chat_message.tool_calls:
            tool_call = smolagents.ToolCall(
                name=chat_tool_call.function.name,
                arguments=chat_tool_call.function.arguments,
                id=chat_tool_call.id
            )
            yield tool_call
            parallel_calls[tool_call.id] = tool_call

        # Process tool calls and call OUR process_single_tool_call
        outputs = {}
        for tool_call in parallel_calls.values():
            tool_output = self.process_single_tool_call(tool_call)
            outputs[tool_output.id] = tool_output
            yield tool_output

        # Update memory step
        memory_step.tool_calls = [parallel_calls[k] for k in sorted(parallel_calls.keys())]
        memory_step.observations = memory_step.observations or ""
        for tool_output in [outputs[k] for k in sorted(outputs.keys())]:
            memory_step.observations += tool_output.observation + "\n"
        memory_step.observations = (
            memory_step.observations.rstrip("\n") if memory_step.observations else memory_step.observations
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

    def run(self, task: str, **kwargs) -> smolagents.RunResult:
        """
        Run the agent on a task.
        
        If the agent completes without calling final_answer, record the
        final response as an summary action.
        """
        try:
            result = super().run(task, **kwargs)
            return result
        except Exception as e:
            # Record error if agent fails
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=None,
                    arguments={},
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    content=f'Agent error: {e}'
                )
            raise
