import logging
import yaml
import importlib
import json
import uuid
import datetime
from smolagents.monitoring import LogLevel
from seaqa_ai import config
from seaqa_ai.prompts.ask import SYSTEM_PROMPT as ASK_SYSTEM_PROMPT
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import ChatToolCalls
from seaqa_ai.agent.utils import tool_output, save_tool_call
from seaqa_ai.agent.memory import build_memory_from_db
from seaqa_ai.agent.memory.openai_memory import OpenAIMemory
from seaqa_ai.agent.tool_calling_agent import ToolCallingAgent
from seaqa_ai.utils import smolagent_steps_to_thought_process, parse_json, object_to_json_str, localize_time, \
    convert_to_json_code_block, get_answer_and_sources, combine_attachments_to_message
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP
from seaqa_ai.utils.llm_api import LLMAPI

logger = logging.getLogger(__name__)

class AgentManager:
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)

    def execute(self, agent_name, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        message_id = context.get('message_id')

        # agent mode
        if agent_name == AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            
            server_model = kwargs.get('server_model')
            agent = ToolCallingAgent(
                self.db_session_class,
                session_uuid,
                message_id,
                prompt_templates=yaml.safe_load(
                    importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
                ),
                tools=tool_executor.tools,  # List of tools available to the agent
                model=server_model,
                max_steps=5,  # Limit the number of reasoning steps
                verbosity_level=LogLevel.DEBUG,  # Show detailed agent reasoning debug
                return_full_result=True,
            )

            if session_uuid and not config.DISABLE_CONTEXT:
                try:
                    agent.memory = build_memory_from_db(AGENT_MODE_PROPERTIES_MAP.MEMORY.value, self.db_session_class, agent.prompt_templates["system_prompt"], session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')

            agent_output = agent.run(combine_attachments_to_message(attachments, message), reset=config.DISABLE_CONTEXT)
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

            answer, sources = get_answer_and_sources(tool_executor, agent_output.output)

            for message in agent_output.messages:
                if (error := message.get('error', {})) and error.get('type') == 'AgentMaxStepsError':
                    with self.db_session_class() as db_session:
                        record = db_session.query(ChatToolCalls).filter(ChatToolCalls.session_uuid==session_uuid, ChatToolCalls.message_id==message_id).first()
                        new_tool_calls = {
                            tool_calls_id: tool_calls
                            for tool_calls_id, tool_calls in record.to_dict()['tool_calls'].items() if 'is_final_answer' not in tool_calls
                        }
                        new_tool_calls['tool_calls_final_answer'] = {
                            'is_final_answer': True,
                            'reach_max_steps': True,
                            'result': object_to_json_str(message.get('action_output')),
                            'static': {
                                'token_usage': message.get('token_usage', {})
                            },
                            'time': localize_time(datetime.datetime.now()).isoformat()
                        }
                        record.tool_calls = json.dumps(new_tool_calls)
                        db_session.commit()

            return {
                'answer': answer,
                'thought_process': smolagent_steps_to_thought_process(agent_output.messages, message, attachments),
                'sources':sources
            }

        # ask mode
        elif agent_name == ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            model=kwargs.get('model')
            llm_type=kwargs.get('llm_type')
            llm_url=kwargs.get('llm_url')
            llm_key=kwargs.get('llm_key')
            temperature=kwargs.get('temperature', 0.1)

            if 'gpt-5' in model:
                temperature = 1
            context=kwargs.get('context')
            if session_uuid and not config.DISABLE_CONTEXT:
                try:
                    memory = build_memory_from_db(ASK_MODE_PROPERTIES_MAP.MEMORY.value, self.db_session_class, ASK_SYSTEM_PROMPT, session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')
                    memory = OpenAIMemory(ASK_SYSTEM_PROMPT)
            else:
                memory = OpenAIMemory(ASK_SYSTEM_PROMPT)

            thought_process = memory.context_thought_process
            user_row_message = combine_attachments_to_message(attachments, message)

            memory += {
                'role': 'user', 
                'content': user_row_message
            }

            thought_process['task']['user_input'] = {
                'message': message,
                'attachments': attachments
            }
            thought_process['task']['user_row_message'] = user_row_message

            llm_api = LLMAPI(data_logger=self.app.data_logger, llm_type=llm_type, base_url=llm_url, api_key=llm_key, model=model)

            response = llm_api.completion(
                messages=memory,
                context=context,
                tools=tool_executor.get(),
                temperature=temperature,
            ).choices[0].message

            if response.tool_calls:
                memory += {
                    'role': 'assistant',
                    'tool_calls': response.tool_calls
                }

                thought_process['tool_calls'] = []
                fix_tool_calls_id = f'tool_calls_group_{uuid.uuid4().hex[:16]}'
                for tool_call in response.tool_calls:
                    arguments = tool_call['function']['arguments']
                    if isinstance(arguments, str):
                        arguments = parse_json(arguments)
                    new_tool_call_to_thought_process = {
                        'name': tool_call['function']['name'],
                        'arguments': arguments
                    }
                    try:
                        to_observation, to_db = tool_output(tool_executor.execute(tool_call))

                        save_tool_call(self.db_session_class, session_uuid, message_id, tool_call, to_db, fix_tool_calls_id=fix_tool_calls_id)

                        try:
                            json_object = json.loads(to_observation)
                        except:
                            json_object = to_observation

                        json_code_block = convert_to_json_code_block(json_object)
                        new_tool_call_to_thought_process['output'] = json_code_block

                        memory += {
                            'role': 'tool',
                            'tool_call_id': tool_call['id'],
                            'content': json.dumps(to_observation)
                        }
                    except Exception as e:
                        logger.exception(f'Execute tool call failure: {e}')
                        save_tool_call(self.db_session_class, session_uuid, message_id, tool_call, error=e, fix_tool_calls_id=fix_tool_calls_id)
                        error_type = type(e).__name__
                        error_message = repr(e)
                        new_tool_call_to_thought_process['error'] = {
                            'type': error_type,
                            'message': error_message
                        }
                        memory += {
                            'role': 'tool',
                            'tool_call_id': tool_call['id'],
                            'content': f'{error_type}: {error_message}'
                        }
                    thought_process['tool_calls'].append(new_tool_call_to_thought_process)

                content = llm_api.completion(
                        messages=memory,
                        context=context,
                        temperature=temperature,
                        model=model
                    ).choices[0].message.content

            else:
                content = response.content

            # filter invalid references
            answer, sources = get_answer_and_sources(tool_executor, content)

            # result to thought process
            thought_process['result'] = content
            
            with self.db_session_class() as db_session:
                record = db_session.query(ChatToolCalls).filter(ChatToolCalls.session_uuid==session_uuid, ChatToolCalls.message_id==message_id).first()
                if record:
                    new_tool_calls = record.to_dict()['tool_calls']
                    new_tool_calls['result'] = content
                    record.tool_calls = json.dumps(new_tool_calls)
                else:
                    record = ChatToolCalls(session_uuid, message_id, {
                        'result': content
                    })
                    db_session.add(record)
                db_session.commit()

            return {
                'answer': answer,
                'thought_process': thought_process,
                'sources': sources
            }

        else:
            raise AssertionError(f"agent_name only receive values `{AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value}` and `{ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value}`")
