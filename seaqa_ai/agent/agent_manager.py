import logging
import yaml
import importlib
import json

from smolagents.monitoring import LogLevel
from seaqa_ai import config
from seaqa_ai.prompts.ask import system_prompts, response_format
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.utils import tool_output, save_tool_call, get_search_results_from_db
from seaqa_ai.agent.memory import build_memory_from_db
from seaqa_ai.agent.memory.openai_memory import OpenAIMemory
from seaqa_ai.agent.tool_calling_agent import ToolCallingAgent
from seaqa_ai.agent.tool_shared_vars import ToolSharedVars
from seaqa_ai.utils import format_agent_memory, parse_json
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP

logger = logging.getLogger(__name__)
TMEPERATURE = 0.1 if 'GPT-5' not in config.LLM_MODEL else 1

class AgentManager:
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        
    def _extra_sources(self, chat_uuid, document_indexes):
        sources = []
        if config.ENABLE_TOOLS_SHARED_VARS:
            # fetch all search results from global vars
            sources_results = ToolSharedVars.get('sources_results', [])
        else:
            # fetch from db
            sources_results = get_search_results_from_db(self.db_session_class, chat_uuid)

        for doc_index in document_indexes:
            if doc_index < len(sources_results):
                sources.append(sources_results[doc_index])
        return sources

    def execute(self, agent_name, message, **kwargs):
        context = kwargs.get('context', {})
        session_id = context.get('session_id')
        chat_uuid = context.get('chat_uuid')

        if config.ENABLE_TOOLS_SHARED_VARS:
            ToolSharedVars.init()

        # agent mode
        if agent_name == AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            tool_executor = kwargs.get('tool_executor')
            server_model = kwargs.get('server_model')
            agent = ToolCallingAgent(
                self.db_session_class,
                chat_uuid,
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

            output = parse_json(agent_output.output)
            if not isinstance(output, dict):
                output = {
                    'answer': output,
                    'document_indexes': []
                }

            document_indexes = output.get('document_indexes', [])
            if not isinstance(document_indexes, list):
                document_indexes = []

            return output.get('answer', ''), format_agent_memory(agent_output.messages), self._extra_sources(chat_uuid, document_indexes)

        # ask mode
        elif agent_name == ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            tool_executor=kwargs.get('tool_executor')
            temperature=kwargs.get('temperature', TMEPERATURE)
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

                for tool_call in response.tool_calls:
                    to_observation, to_tools_shared_vars, to_db = tool_output(tool_executor.execute(tool_call))

                    if config.ENABLE_TOOLS_SHARED_VARS and to_tools_shared_vars:
                        ToolSharedVars.update(to_tools_shared_vars)
                    
                    save_tool_call(self.db_session_class, chat_uuid, tool_call, to_db)

                    memory += {
                        'role': 'tool',
                        'tool_call_id': tool_call['id'],
                        'content': json.dumps(to_observation)
                    }

                content = self.app.llm_api.completion(
                        messages=memory,
                        context=context,
                        temperature=temperature,
                    ).choices[0].message.content
            else:
                content = response.content
            
            content = parse_json(content)
            if not isinstance(content, dict):
                content = {
                    'answer': content,
                    'document_indexes': []
                }
            
            if not isinstance(content['answer'], str):
                content['answer'] = json.dumps(content['answer'])

            return content.get('answer', ''), self._extra_sources(chat_uuid, content.get('document_indexes', []))
