import logging
import yaml
import importlib
import json
import ast

from smolagents import ToolCallingAgent
from smolagents.monitoring import LogLevel
from smolagents.agent_types import AgentText
from seaqa_ai import config
from seaqa_ai.prompts.ask import system_prompts, response_format
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.memory import build_memory_from_db
from seaqa_ai.agent.memory.openai_memory import OpenAIMemory
from seaqa_ai.utils import format_agent_memory, clean_json_markdown
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP


logger = logging.getLogger(__name__)
                

class AgentManager:
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)

    def execute(self, agent_name, message, **kwargs):
        context = kwargs.get('context', {})
        session_id = context.get('session_id')

        # agent mode
        if agent_name == AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            tool_executor = kwargs.get('tool_executor')
            server_model = kwargs.get('server_model')
            agent = ToolCallingAgent(
                prompt_templates=yaml.safe_load(
                    importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
                ),
                tools=tool_executor.tools if tool_executor else [],  # List of tools available to the agent
                model=server_model,
                max_steps=3,  # Limit the number of reasoning steps
                verbosity_level=LogLevel.DEBUG,  # Show detailed agent reasoning debug
                return_full_result=True,
            )

            if session_id and not config.DISABLE_CONTEXT:
                try:
                    agent.memory = build_memory_from_db(AGENT_MODE_PROPERTIES_MAP.MEMORY.value, self.db_session_class, agent.prompt_templates["system_prompt"], session_id, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')

            agent_output = agent.run(message, reset=config.DISABLE_CONTEXT)
            for msg in agent_output.messages:
                model_input_messages = msg.get('model_input_messages')
                if model_input_messages:
                    msg['model_input_messages'] = [input_msg.dict() for input_msg in msg.get('model_input_messages')]

                model_output_message = msg.get('model_output_message')
                if model_output_message:
                    model_output_message.pop('raw', None)

                model_output = msg.get('model_output')
                if model_output:
                    msg['model_output'] = [model_output_msg.dict() for model_output_msg in model_output]

            output = agent_output.output
            if isinstance(output, AgentText):
                try:
                    output = ast.literal_eval(output)
                except:
                    try:
                        output = clean_json_markdown(output)
                        output = ast.literal_eval(output)
                    except:
                        output = {
                            'answer': output,
                            'sources': []
                        }
            sources = output.get('sources', [])
            try:
                sources = ast.literal_eval(sources) if isinstance(sources, str) else sources
            except:
                sources = []
            return output.get('answer', ''), format_agent_memory(agent_output.messages), sources

        # ask mode
        elif agent_name == ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            tool_executor=kwargs.get('tool_executor')
            temperature=kwargs.get('temperature', 0.1)
            if 'gpt-5' in config.LLM_MODEL:
                temperature = 1
            context=kwargs.get('context')
            if session_id and not config.DISABLE_CONTEXT:
                try:
                    memory = build_memory_from_db(ASK_MODE_PROPERTIES_MAP.MEMORY.value, self.db_session_class, system_prompts, session_id, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')
                    memory = OpenAIMemory(system_prompts)
            else:
                memory = OpenAIMemory(system_prompts)

            memory += {
                'role': 'user', 
                'content': message + f'\n\nPlease **ensure** that a json string that can be parsed is returned in the following format (to **ensure that no parsing failure errors occur**) for your response: {response_format}'
            }

            response = self.app.llm_api.completion(
                messages=memory,
                context=context,
                tools=tool_executor.get() if tool_executor else [],
                temperature=temperature
            ).choices[0].message

            if response.tool_calls:
                memory += {
                    'role': 'assistant',
                    'tool_calls': response.tool_calls
                }

                memory += [
                    {
                        'role': 'tool',
                        'tool_call_id': tool_call['id'],
                        'content': json.dumps(tool_executor.execute(tool_call))
                    }
                    for tool_call in response.tool_calls
                ]

                content = self.app.llm_api.completion(
                        messages=memory,
                        context=context,
                        temperature=temperature,
                    ).choices[0].message.content
            else:
                content = response.content

            try:
                content = ast.literal_eval(content)
                return content['answer'], content['sources']
            except:
                try:
                    content = clean_json_markdown(content)
                    content = ast.literal_eval(content)
                    return content['answer'], content['sources']
                except Exception as e:
                    logger.warning(f"Failure to parse json: {str(e)}")
                    return content, []
