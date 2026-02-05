import logging
import yaml
import importlib
import json
import time
from smolagents import LogLevel, ChatMessageStreamDelta, ActionStep, FinalAnswerStep
from seaqa_ai import config
from seaqa_ai.prompts.ask import SYSTEM_PROMPT as ASK_SYSTEM_PROMPT
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.memory import build_memory_from_db
from seaqa_ai.agent.memory.openai_memory import OpenAIMemory
from seaqa_ai.agent.tools import TestTool
from seaqa_ai.agent.tools.content_generators import GenerateMarkdown
from seaqa_ai.agent.tools.search_tools import DocumentsSearch, IssuesSearch
from seaqa_ai.agent.tool_calling_agent import ToolCallingAgent
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.utils import parse_json, object_to_json_str, get_answer_and_sources, \
    combine_attachments_to_message, remove_attachments_content, filter_tool_calls_content, \
    remove_sources_content_and_snippets
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP
from seaqa_ai.utils.llm_api import LiteLLMModel, get_llm_client_by_model_id
from seaqa_ai.utils.sse import SSE

logger = logging.getLogger(__name__)

class BasicChatManager(object):
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.search_tools = (
            DocumentsSearch(app),
            IssuesSearch(app),
        )
        self.content_generators = (
            GenerateMarkdown(app),
        )
        self.test_tool = TestTool(app)

    def _register_tools(self, tool_executor, context):
        for tool in self.search_tools:
            tool.register(tool_executor, context=context)
        for tool in self.content_generators:
            tool.register(tool_executor)
        if config.ENABLE_TEST_TOOL:
            self.test_tool.register(tool_executor)

    def run(self, model, tool_executor, message, attachments, **kwargs):
        raise NotImplementedError((model, tool_executor, message, attachments, kwargs))
    
    def streamly_run(self, model, tool_executor, message, attachments, **kwargs):
        raise NotImplementedError((model, tool_executor, message, attachments, kwargs))
    
    def _get_tool_executor(self):
        raise NotImplementedError()
    
    def __call__(self, message, attachments, context, model, stream=False):
        tool_executor = self._get_tool_executor()
        self._register_tools(tool_executor, context)
        try:
            if stream:
                return self.streamly_run(model, tool_executor, message, attachments, context=context)
            return self.run(model, tool_executor, message, attachments, context=context)
        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"
    
class NormalChatManager(BasicChatManager):
    # normal mode
    def __init__(self, app):
        super().__init__(app)
        self._get_tool_executor = FunctionCallingToolExecutor
    
    def run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
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

        current_step = 0
        while current_step < config.MAX_STEPS:
            current_step += 1
            time_begin = time.time()
            response = llm_client.completion(
                messages=memory,
                context=context,
                tools=tool_executor.get() if current_step < config.MAX_STEPS else None
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
                    'sources': remove_sources_content_and_snippets(sources)
                }
        raise AssertionError('Invalid AI responses led to unexpected steps.')
    
    def streamly_run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
        context=kwargs.get('context')
        yield SSE.status('Preparing', 'Initializing user input')
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

        current_step = 0
        while current_step < config.MAX_STEPS:
            yield SSE.status('LLM Reasoning')
            current_step += 1
            time_begin = time.time()
            response = llm_client.completion(
                messages=memory,
                context=context,
                tools=tool_executor.get() if current_step < config.MAX_STEPS else None,
                stream=True
            )

            tool_calls = []
            content = ""
            current_tool_index = -1
            token_usage = {}
            has_yield_answering_status = False

            for chunk in response:
                if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                    for tool_call in chunk.tool_calls:
                        if tool_call.type == 'function':
                            tool_calls.append({
                                'id': tool_call.id,
                                'type': 'function',
                                'function': {
                                    'name': tool_call.function.name,
                                    'arguments': tool_call.function.arguments
                                }
                            })
                elif hasattr(chunk, 'choices') and chunk.choices:
                    delta = chunk.choices[0].delta
                    
                    if hasattr(delta, 'tool_calls') and delta.tool_calls:
                        for tool_call_delta in delta.tool_calls:
                            if tool_call_delta.index is not None and tool_call_delta.index > current_tool_index:
                                current_tool_index = tool_call_delta.index
                                tool_calls.append({
                                    'id': tool_call_delta.id or f"call_{tool_call_delta.index}",
                                    'type': 'function',
                                    'function': {
                                        'name': '',
                                        'arguments': ''
                                    }
                                })
                            
                            if hasattr(tool_call_delta, 'function') and tool_call_delta.function:
                                if tool_call_delta.function.name:
                                    tool_calls[current_tool_index]['function']['name'] = tool_call_delta.function.name
                                if tool_call_delta.function.arguments:
                                    tool_calls[current_tool_index]['function']['arguments'] += tool_call_delta.function.arguments

                    elif hasattr(delta, 'content') and delta.content:
                        if not has_yield_answering_status:
                            yield SSE.status('Answering')
                            has_yield_answering_status = True
                        yield SSE.answer(delta.content)
                        content += delta.content
                if hasattr(chunk, 'usage'):
                    token_usage = {
                        'input_tokens': chunk.usage.prompt_tokens,
                        'output_tokens': chunk.usage.completion_tokens,
                        'total_tokens': chunk.usage.total_tokens,
                    }

            # action step
            if tool_calls:
                has_search_tool = False

                yield SSE.status('Calling tools', tool_calls)
                memory += {
                    'role': 'assistant',
                    'tool_calls': tool_calls
                }
                tool_executor.thought_process.add_tool_calls_group()
                for tool_call in tool_calls:
                    tool_name = tool_call['function']['name']
                    arguments = tool_call['function']['arguments']

                    if tool_name in ('documents_search', 'issues_search'):
                        has_search_tool = True
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
                tool_executor.thought_process.update_last_group_tokens_usage(token_usage)
                tool_executor.thought_process.set_last_group_time_usage(time.time() - time_begin)

                if has_search_tool and (found_records := len(tool_executor.cache.get('sources_results', []))):
                    yield SSE.search_found(found_records)

            # final answer
            else:
                tool_executor.thought_process.set_final_answer(content, current_step >= config.MAX_STEPS, token_usage, time.time() - time_begin)

                # filter invalid references
                answer, sources = get_answer_and_sources(tool_executor, content)
            
                yield SSE.results(answer, remove_sources_content_and_snippets(sources), tool_executor.thought_process.details)
                yield SSE.done()
                break

class ReasoningChatManager(BasicChatManager):
    # normal mode
    def __init__(self, app):
        super().__init__(app)
        self._get_tool_executor = SmolAgentsToolExecutor

    def run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
        prompt_templates = yaml.safe_load(
            importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
        )
        prompt_templates['system_prompt'] = prompt_templates['system_prompt'].replace("{{MAX_STEPS}}", str(config.MAX_STEPS))

        agent = ToolCallingAgent(
            self.db_session_class,
            session_uuid,
            prompt_templates=prompt_templates,
            tool_executor=tool_executor,  # List of tools available to the agent
            model=LiteLLMModel(client=llm_client, context=context),
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
            'sources': remove_sources_content_and_snippets(sources)
        }
    
    def streamly_run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
        prompt_templates = yaml.safe_load(
            importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
        )
        prompt_templates['system_prompt'] = prompt_templates['system_prompt'].replace("{{MAX_STEPS}}", str(config.MAX_STEPS))

        agent = ToolCallingAgent(
            self.db_session_class,
            session_uuid,
            prompt_templates=prompt_templates,
            tool_executor=tool_executor,  # List of tools available to the agent
            model=LiteLLMModel(client=llm_client, context=context),
            max_steps=config.MAX_STEPS,  # Limit the number of reasoning steps
            verbosity_level=LogLevel.DEBUG,  # Show detailed agent reasoning debug
            return_full_result=True,
            stream_outputs=True
        )

        yield SSE.status('Preparing', 'Initializing user input')

        user_raw_message = combine_attachments_to_message(attachments, message)
        tool_executor.thought_process.set_task(prompt_templates['system_prompt'], user_raw_message, message, remove_attachments_content(attachments))

        if session_uuid and not config.DISABLE_CONTEXT:
            try:
                agent.memory = build_memory_from_db(AGENT_MODE_PROPERTIES_MAP.MEMORY.value, self.db_session_class, prompt_templates['system_prompt'], session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                tool_executor.thought_process.context = agent.memory.context_thought_process
            except Exception as e:
                logger.warning(f'Failure to build context: {e}')

        
        yield SSE.status('LLM Reasoning')
        reach_max_steps = False
        tool_calls_name = []
        is_final_answer_tool_call = False
        answer_last_index = 12 # len('{"answer": "')
        answer_history = ''
        final_answer_tool_call_index = 0
        last_tool_calls_has_search_tools = False
        has_yield_tool_call = False
        for step in agent.run(user_raw_message, stream=True, reset=config.DISABLE_CONTEXT):
            if isinstance(step, ChatMessageStreamDelta):
                if step.tool_calls:
                    tool_call_name_completed = True
                    for i, tool_call in enumerate(step.tool_calls):
                        if tool_call.function.name:
                            tool_call_name_completed = False
                            if i == len(tool_calls_name):
                                tool_calls_name.append(tool_call.function.name)
                            else:
                                tool_calls_name[i] += tool_call.function.name

                    if tool_call_name_completed and not has_yield_tool_call:
                        has_yield_tool_call = True
                        yield SSE.status('Calling tools', tool_calls_name)

                    # check has final_answer
                    if not is_final_answer_tool_call and 'final_answer' in tool_calls_name:
                        is_final_answer_tool_call = True
                        yield SSE.status('Answering')
                        for i, tool_call_name in enumerate(tool_calls_name):
                            if tool_call_name == 'final_answer':
                                final_answer_tool_call_index = i
                                break
                    # check has search tools
                    elif 'documents_search' in tool_calls_name or 'issues_search' in tool_calls_name:
                        last_tool_calls_has_search_tools = True

                    # yield answer
                    if is_final_answer_tool_call and step.tool_calls[final_answer_tool_call_index].function.arguments:
                        answer_history += step.tool_calls[final_answer_tool_call_index].function.arguments
                        if answer_history.endswith('"'):
                            yield SSE.answer(answer_history[answer_last_index:-1])
                            answer_last_index = max(11, len(answer_history) - 1)
                        elif answer_history.endswith('}'):
                            try:
                                # also means all answer has output
                                json.loads(answer_history)
                                yield SSE.answer(answer_history[answer_last_index:-2])
                                answer_last_index = max(11, len(answer_history) - 2)
                            except:
                                # not end
                                yield SSE.answer(answer_history[answer_last_index:])
                                answer_last_index = max(11, len(answer_history))
                        else:
                            yield SSE.answer(answer_history[answer_last_index:])
                            answer_last_index = max(11, len(answer_history))
            elif isinstance(step, ActionStep):
                # yield tool output if has search_tools and found some records
                if last_tool_calls_has_search_tools and (found_records := len(tool_executor.cache.get('sources_results', []))):
                    yield SSE.search_found(found_records)

                if not is_final_answer_tool_call:
                    yield SSE.status('LLM Reasoning')

                # reset delta properties
                reach_max_steps = False
                tool_calls_name = []
                is_final_answer_tool_call = False
                final_answer_tool_call_index = 0
                answer_last_index = 11
                answer_history = ''
                last_tool_calls_has_search_tools = False
                has_yield_tool_call = False

                # record to thought process
                reach_max_steps = step.step_number == config.MAX_STEPS
                tool_executor.thought_process.actions[step.step_number - 1]['time_usage'] = step.timing.duration
                tool_executor.thought_process.actions[step.step_number - 1]['token_usage'] = step.token_usage.dict()
                if step.error:
                    tool_executor.thought_process.actions[step.step_number - 1]['error'] = step.error.dict()
            # Always appearing as the last step, yield final answer
            elif isinstance(step, FinalAnswerStep):
                if reach_max_steps:
                    tool_executor.thought_process.set_final_answer(step.output, True)

                answer, sources = get_answer_and_sources(tool_executor, step.output)
                yield SSE.results(answer, remove_sources_content_and_snippets(sources), tool_executor.thought_process.details)
                yield SSE.done()
