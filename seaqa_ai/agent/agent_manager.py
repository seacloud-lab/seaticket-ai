import logging
import yaml
import importlib
import json
import time
from smolagents import LogLevel
from seaqa_ai import config
from seaqa_ai.prompts.ask import SYSTEM_PROMPT as ASK_SYSTEM_PROMPT
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.memory import build_memory_from_db
from seaqa_ai.agent.memory.openai_memory import OpenAIMemory
from seaqa_ai.agent.tool_calling_agent import ToolCallingAgent
from seaqa_ai.utils import parse_json, object_to_json_str, get_answer_and_sources, combine_attachments_to_message, remove_attachments_content, filter_tool_calls_content
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

        # agent mode
        if agent_name == AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value:
            
            server_model = kwargs.get('server_model')
            prompt_templates = yaml.safe_load(
                importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
            )
            prompt_templates['system_prompt'] = prompt_templates['system_prompt'].replace("{{MAX_STEPS}}", str(config.MAX_STEPS))
            agent = ToolCallingAgent(
                self.db_session_class,
                session_uuid,
                prompt_templates=prompt_templates,
                tool_executor=tool_executor,  # List of tools available to the agent
                model=server_model,
                max_steps=config.MAX_STEPS,  # Limit the number of reasoning steps
                verbosity_level=LogLevel.DEBUG,  # Show detailed agent reasoning debug
                return_full_result=True,
            )

            user_raw_message = combine_attachments_to_message(attachments, message)
            tool_executor.thought_process.set_task(prompt_templates['system_prompt'], user_raw_message, message, remove_attachments_content(attachments))

            if session_uuid and not config.DISABLE_CONTEXT:
                try:
                    agent.memory = build_memory_from_db(AGENT_MODE_PROPERTIES_MAP.MEMORY.value, self.db_session_class, prompt_templates['system_prompt'], session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                    tool_executor.thought_process.context = agent.memory.context_thought_process
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')

            agent_output = agent.run(user_raw_message, reset=config.DISABLE_CONTEXT)

            answer, sources = get_answer_and_sources(tool_executor, agent_output.output)

            # set time_usage & max step
            for agent_message in agent_output.messages:
                step_number = agent_message.get('step_number')
                if not step_number:
                    continue
                step_number -= 1
                if agent_message.get('tool_calls') or agent_message.get('error'):
                    error = agent_message.get('error', {})
                    if error and error.get('type') == 'AgentMaxStepsError':
                        # final answer
                        tool_executor.thought_process.set_final_answer(object_to_json_str(agent_message.get('action_output')), True, agent_message.get('token_usage', {}), agent_message.get('timing', {}).get('duration', 0))    
                    else:
                        # actions step -> a group in thought process
                        tool_executor.thought_process.actions[step_number]['time_usage'] = agent_message.get('timing', {}).get('duration', 0)
                        tool_executor.thought_process.actions[step_number]['token_usage'] = agent_message.get('token_usage', {
                            'input_tokens': 0,
                            'output_tokens': 0,
                            'total_tokens': 0
                        })
                        if error:
                            tool_executor.thought_process.actions[step_number]['error'] = error   

            return {
                'answer': answer,
                'thought_process': tool_executor.thought_process.details,
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

            user_raw_message = combine_attachments_to_message(attachments, message)
            tool_executor.thought_process.set_task(ASK_SYSTEM_PROMPT, user_raw_message, message, remove_attachments_content(attachments))
            tool_executor.thought_process.context = memory.context_thought_process

            memory += {
                'role': 'user', 
                'content': user_raw_message
            }

            llm_api = LLMAPI(data_logger=self.app.data_logger, llm_type=llm_type, base_url=llm_url, api_key=llm_key, model=model)

            current_step = 0
            while current_step < config.MAX_STEPS:
                current_step += 1
                time_begin = time.time()
                response = llm_api.completion(
                    messages=memory,
                    context=context,
                    tools=tool_executor.get() if current_step < config.MAX_STEPS else None,
                    temperature=temperature
                )

                try:
                    llm_message = response.choices[0].message
                except:
                    raise AssertionError('Invalid AI responses')

                # action step
                if llm_message.tool_calls:
                    memory += {
                        'role': 'assistant',
                        'tool_calls': llm_message.tool_calls
                    }
                    tool_executor.thought_process.add_tool_calls_group()
                    for tool_call in llm_message.tool_calls:
                        tool_name = tool_call['function']['name']
                        arguments = tool_call['function']['arguments']
                        if isinstance(arguments, str):
                            arguments = parse_json(arguments)
                        try:
                            tool_output = tool_executor.execute(tool_call)
                            tool_executor.thought_process.add_tool_call(tool_name, arguments, filter_tool_calls_content(tool_name, tool_output))
                            memory += {
                                'role': 'tool',
                                'tool_call_id': tool_call['id'],
                                'content': json.dumps(tool_output)
                            }
                        except Exception as e:
                            logger.exception(f'Execute tool call failure: {e}')
                            error_type = type(e).__name__
                            error_message = repr(e)
                            tool_executor.thought_process.add_tool_call(tool_name, arguments)
                            tool_executor.thought_process.set_last_group_error({
                                'type': error_type,
                                'message': error_message
                            })
                            memory += {
                                'role': 'tool',
                                'tool_call_id': tool_call['id'],
                                'content': f'{error_type}: {error_message}'
                            }
                    tool_executor.thought_process.update_last_group_tokens_usage({
                        'input_tokens': response.usage.prompt_tokens,
                        'output_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens,
                    })
                    tool_executor.thought_process.set_last_group_time_usage(time.time() - time_begin)

                # final answer
                else:
                    content = llm_message.content
                    tool_executor.thought_process.set_final_answer(content, current_step >= config.MAX_STEPS, {
                        'input_tokens': response.usage.prompt_tokens,
                        'output_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens,
                    }, time.time() - time_begin)

                    # filter invalid references
                    answer, sources = get_answer_and_sources(tool_executor, content)

                    return {
                        'answer': answer,
                        'thought_process': tool_executor.thought_process.details,
                        'sources': sources
                    }
            raise AssertionError('Invalid AI responses led to unexpected steps.')

        else:
            raise AssertionError(f"agent_name only receive values `{AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value}` and `{ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value}`")
