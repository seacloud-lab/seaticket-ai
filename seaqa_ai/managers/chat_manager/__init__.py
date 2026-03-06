import logging
import json
import time
from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.managers.chat_manager.memory import build_memory_from_db, OpenAIMemory
from seaqa_ai.managers.chat_manager.tools import ToolExecutor, TestTool, DocumentsSearch, IssuesSearch, GenerateMarkdown
from seaqa_ai.managers.chat_manager.system_prompt import SYSTEM_PROMPT
from seaqa_ai.utils import parse_json, get_answer_and_sources, \
    combine_attachments_to_message, remove_attachments_content, filter_tool_calls_content, \
    remove_sources_content_and_snippets
from seaqa_ai.utils.llm_api import get_llm_client_by_model_id
from seaqa_ai.utils.sse import SSE

logger = logging.getLogger(__name__)

class BasicChat(object):
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
    
    def _build_system_prompt(self, base_prompt, project_prompt):
        """
        Merge project-level prompt with base system prompt.
        If project_prompt is provided, it will be prepended to the base prompt
        to provide project-specific context for all conversations.
        """
        if not project_prompt or not project_prompt.strip():
            return base_prompt
        
        return f"""Project Context:
{project_prompt}

---------------

{base_prompt}"""
    
    def __call__(self, message, attachments, context, model):
        tool_executor = ToolExecutor()
        self._register_tools(tool_executor, context)
        return self.run(model, tool_executor, message, attachments, context=context)

    def run(self, model, tool_executor, message, attachments, **kwargs):
        raise NotImplementedError()

class SyncChat(BasicChat):
    def run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        project_prompt = context.get('project_prompt', '')
        llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
        context=kwargs.get('context')
        
        system_prompt = self._build_system_prompt(SYSTEM_PROMPT, project_prompt)
        
        if session_uuid and not config.DISABLE_CONTEXT:
            try:
                memory = build_memory_from_db(self.db_session_class, system_prompt, session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
            except Exception as e:
                logger.warning(f'Failure to build context: {e}')
                memory = OpenAIMemory(system_prompt)
        else:
            memory = OpenAIMemory(system_prompt)

        user_raw_message = combine_attachments_to_message(attachments, message)
        tool_executor.thought_process.set_task(system_prompt, user_raw_message, message, remove_attachments_content(attachments))
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

class StreamingChat(BasicChat):
    def run(self, model, tool_executor, message, attachments, **kwargs):
        try:
            context = kwargs.get('context', {})
            session_uuid = context.get('session_uuid')
            project_prompt = context.get('project_prompt', '')
            llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
            yield SSE.status('Preparing', 'Initializing user input')
            
            system_prompt = self._build_system_prompt(SYSTEM_PROMPT, project_prompt)
            
            if session_uuid and not config.DISABLE_CONTEXT:
                try:
                    memory = build_memory_from_db(self.db_session_class, system_prompt, session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME)
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')
                    memory = OpenAIMemory(system_prompt)
            else:
                memory = OpenAIMemory(system_prompt)

            user_raw_message = combine_attachments_to_message(attachments, message)
            tool_executor.thought_process.set_task(system_prompt, user_raw_message, message, remove_attachments_content(attachments))
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
                    stream=True,
                    stream_options={
                        "include_usage": True
                    }
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
                        llm_client.logger_usage(token_usage, context)

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
        except Exception as e:
            logger.exception(f'Streaming run AI failure: {e}')
            yield SSE.error(repr(e))
            yield SSE.done()
