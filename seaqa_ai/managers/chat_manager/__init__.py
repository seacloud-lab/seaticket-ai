import logging
import json
import time
from collections import deque
from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import ProjectConnection, PortalChatMessages, ChatMessages
from seaqa_ai.utils import uuid_str_to_32_chars, remove_reference_markers, remove_sources_content_and_snippets, build_system_prompt
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.llm_api import get_llm_client_by_model_id
from seaqa_ai.utils.sse import SSE
from seaqa_ai.utils.tools import OpenAIToolExecutor
from seaqa_ai.utils.completion import BasicCompletionUtils, StreamingCompletionUtils
from seaqa_ai.managers.chat_manager.memory import build_memory_from_db, OpenAIMemory
from seaqa_ai.managers.chat_manager.tools import TestTool, DocumentsSearch, IssuesSearch
from seaqa_ai.managers.chat_manager.tools.content_generators import KnowledgeBaseEntryCreator, MarkdownGenerator
from seaqa_ai.managers.chat_manager.system_prompt import SYSTEM_PROMPT
from seaqa_ai.managers.chat_manager.utils import get_answer_and_sources, combine_attachments_to_message, \
    strip_content_details_from_attachments
from seaqa_ai.managers.chat_manager.utils.callbacker import ChatCallBacker

logger = logging.getLogger(__name__)

class BasicChat(object):
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.search_tools = (
            DocumentsSearch(),
            IssuesSearch(),
        )
        self.content_generators = (
            MarkdownGenerator(),
            KnowledgeBaseEntryCreator(),
        )
        self.test_tool = TestTool()

    def _register_tools(self, tool_executor, context):
        project_uuid = uuid_str_to_32_chars(context['project_uuid'])
        connection_ids = context.get('connection_ids', []) or []

        doc_types = set(ConnectionType.get_document_connection_types())
        issue_types = set(ConnectionType.get_issue_connection_types())

        document_connections = []
        issue_connections = []
        if connection_ids:
            with self.db_session_class() as db_session:
                connections = ProjectConnection.get_connections_by_ids(
                    db_session, project_uuid, connection_ids
                )
            for conn in connections:
                if conn['type'] in doc_types:
                    document_connections.append(conn)
                elif conn['type'] in issue_types:
                    issue_connections.append(conn)

        context.update({
            'document_connections': document_connections,
            'issue_connections': issue_connections,
        })

        for tool in self.search_tools:
            tool.register(tool_executor, context=context, app=self.app)
        if not context.get('is_external_portal'):
            for tool in self.content_generators:
                tool.register(tool_executor, context=context)
        if config.ENABLE_TEST_TOOL:
            self.test_tool.register(tool_executor)
    
    def __call__(self, message, attachments, context, model):
        tool_executor = OpenAIToolExecutor()
        self._register_tools(tool_executor, context)
        return self.run(model, tool_executor, message, attachments, context=context, app=self.app)

    def run(self, model, tool_executor, message, attachments, **kwargs):
        raise NotImplementedError()

class SyncChat(BasicChat):
    def run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        project_prompt = context.get('project_prompt', '')
        llm_client = get_llm_client_by_model_id(self.app.data_logger, model)
        context=kwargs.get('context')
        
        system_prompt = build_system_prompt(SYSTEM_PROMPT, project_prompt)
        
        if session_uuid and not config.DISABLE_CONTEXT:
            try:
                message_model = PortalChatMessages if context.get('is_external_portal') else ChatMessages
                memory = build_memory_from_db(self.db_session_class, system_prompt, session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME, message_model=message_model)
            except Exception as e:
                logger.warning(f'Failure to build context: {e}')
                memory = OpenAIMemory(system_prompt)
        else:
            memory = OpenAIMemory(system_prompt)

        user_raw_message = combine_attachments_to_message(attachments, message)
        tool_executor.thought_process.set_task(system_prompt, user_raw_message, message, strip_content_details_from_attachments(attachments))
        tool_executor.thought_process.context = memory.context_thought_process

        memory += {
            'role': 'user', 
            'content': user_raw_message
        }

        current_step = 0
        while current_step < config.MAX_STEPS:
            current_step += 1
            response = None
            completion_retries = []
            for current_retry in range(config.COMPLETION_MAX_RETRIES + 1):
                time_begin = time.time()
                response_token_usage = {}
                try:
                    response = llm_client.completion(
                        messages=memory,
                        context=context,
                        tools=tool_executor.get() if current_step < config.MAX_STEPS else None,
                        num_retries=1
                    )
                    response_token_usage = BasicCompletionUtils.get_response_token_usage(response)
                    break
                except Exception as e:
                    time_usage = time.time() - time_begin
                    completion_retries.append(BasicCompletionUtils.build_completion_retry_entry(
                        current_step,
                        current_retry,
                        'completion_error',
                        time_usage,
                        token_usage=response_token_usage,
                        error=repr(e)
                    ))
                    logger.exception(f'Completion failure at step {current_step}: {e}')
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer('', current_step >= config.MAX_STEPS, response_token_usage, time_usage, retry=completion_retries)

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
                if completion_retries:
                    tool_executor.thought_process.set_last_group_completion_retry(completion_retries)
                for tool_call in llm_message.tool_calls:
                    BasicCompletionUtils.execute_tool_with_retry(tool_executor, tool_call, ChatCallBacker, memory)
                tool_executor.thought_process.update_last_group_tokens_usage(BasicCompletionUtils.get_response_token_usage(response))
                tool_executor.thought_process.set_last_group_time_usage(time.time() - time_begin)

            # final answer
            else:
                content = llm_message.content
                tool_executor.thought_process.set_final_answer(
                    content,
                    current_step >= config.MAX_STEPS,
                    BasicCompletionUtils.get_response_token_usage(response),
                    time.time() - time_begin,
                    retry=completion_retries if completion_retries else None
                )

                # filter invalid references
                answer, sources = get_answer_and_sources(tool_executor, content)

                if context.get('is_external_portal'):
                    answer = remove_reference_markers(answer)
                    sources = []

                return {
                    'answer': answer,
                    'thought_process': {} if context.get('is_external_portal') else tool_executor.thought_process.details,
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
            
            system_prompt = build_system_prompt(SYSTEM_PROMPT, project_prompt)
            
            if session_uuid and not config.DISABLE_CONTEXT:
                try:
                    message_model = PortalChatMessages if context.get('is_external_portal') else ChatMessages
                    memory = build_memory_from_db(self.db_session_class, system_prompt, session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME, message_model=message_model)
                except Exception as e:
                    logger.warning(f'Failure to build context: {e}')
                    memory = OpenAIMemory(system_prompt)
            else:
                memory = OpenAIMemory(system_prompt)

            user_raw_message = combine_attachments_to_message(attachments, message)
            tool_executor.thought_process.set_task(system_prompt, user_raw_message, message, strip_content_details_from_attachments(attachments))
            tool_executor.thought_process.context = memory.context_thought_process

            memory += {
                'role': 'user', 
                'content': user_raw_message
            }

            current_step = 0
            current_retry = 0
            completion_retries = []
            while current_step < config.MAX_STEPS:
                detail = f'step {current_step + 1}'
                if current_retry:
                    detail += f', retrying {current_retry}'
                yield SSE.status('LLM Reasoning', detail)
                time_begin = time.time()
                token_usage = {}
                try:
                    response = llm_client.completion(
                        messages=memory,
                        context=context,
                        tools=tool_executor.get() if current_step + 1 < config.MAX_STEPS else None,
                        stream=True,
                        stream_options={
                            "include_usage": True
                        },
                        num_retries=1
                    )
                except Exception as e:
                    time_usage = time.time() - time_begin
                    completion_retries.append(StreamingCompletionUtils.build_completion_retry_entry(
                        current_step + 1,
                        current_retry,
                        'completion_error',
                        time_usage,
                        token_usage=token_usage,
                        error=repr(e)
                    ))
                    logger.exception(f'Streaming completion failure at step {current_step + 1}: {e}')
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        current_retry += 1
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer('', current_step + 1 >= config.MAX_STEPS, token_usage, time_usage, retry=completion_retries)
                    raise

                tool_calls = []
                content = ""
                current_tool_index = -1
                has_yield_answering_status = False
                recent_chunks = deque(maxlen=5)

                try:
                    for chunk in response:
                        recent_chunks.append(StreamingCompletionUtils.safe_model_dump(chunk))
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
                            choice = chunk.choices[0]
                            delta = choice.delta
                            
                            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                                for tool_call_delta in delta.tool_calls:
                                    tool_call_index = tool_call_delta.index
                                    current_tool_index = StreamingCompletionUtils.ensure_tool_call_slot(tool_calls, tool_call_index, tool_call_delta.id)
                                    
                                    if hasattr(tool_call_delta, 'function') and tool_call_delta.function:
                                        if tool_call_delta.function.name:
                                            tool_calls[current_tool_index]['function']['name'] = tool_call_delta.function.name
                                        if tool_call_delta.function.arguments:
                                            tool_calls[current_tool_index]['function']['arguments'] += tool_call_delta.function.arguments

                            if hasattr(delta, 'function_call') and delta.function_call:
                                current_tool_index = StreamingCompletionUtils.ensure_tool_call_slot(tool_calls, current_tool_index if current_tool_index >= 0 else 0)
                                if delta.function_call.name:
                                    tool_calls[current_tool_index]['function']['name'] = delta.function_call.name
                                if delta.function_call.arguments:
                                    tool_calls[current_tool_index]['function']['arguments'] += delta.function_call.arguments

                            delta_content = StreamingCompletionUtils.collect_delta_content(getattr(delta, 'content', None))
                            if delta_content:
                                if not has_yield_answering_status:
                                    yield SSE.status('Answering')
                                    has_yield_answering_status = True
                                yield SSE.answer(delta_content)
                                content += delta_content
                        if hasattr(chunk, 'usage'):
                            token_usage = {
                                'input_tokens': chunk.usage.prompt_tokens,
                                'output_tokens': chunk.usage.completion_tokens,
                                'total_tokens': chunk.usage.total_tokens,
                            }
                            llm_client.logger_usage(token_usage, context)
                except Exception as e:
                    if tool_calls or content:
                        raise AssertionError('Cannot deal with invalid response')
                    time_usage = time.time() - time_begin
                    completion_retries.append(StreamingCompletionUtils.build_completion_retry_entry(
                        current_step + 1,
                        current_retry,
                        'completion_error',
                        time_usage,
                        token_usage=token_usage,
                        error=repr(e)
                    ))
                    logger.exception(f'Streaming completion chunk failure at step {current_step + 1}: {e}')
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        current_retry += 1
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer('', current_step + 1 >= config.MAX_STEPS, token_usage, time_usage, retry=completion_retries)
                    raise

                if not tool_calls and not content:
                    time_usage = time.time() - time_begin
                    completion_retries.append({
                        **StreamingCompletionUtils.build_completion_retry_entry(
                            current_step + 1,
                            current_retry,
                            'empty_visible_output',
                            time_usage,
                            token_usage=token_usage,
                            error='Invalid response from LLM, at least one of keys tool_calls or content must be visible'
                        )
                    })
                    if token_usage:
                        logger.warning(
                            'Streaming completion returned usage without visible output. model=%s session_uuid=%s project_uuid=%s step=%s retry=%s max_retries=%s recent_chunks=%s',
                            model,
                            session_uuid,
                            context.get('project_uuid'),
                            current_step + 1,
                            current_retry,
                            config.COMPLETION_MAX_RETRIES,
                            json.dumps(list(recent_chunks), ensure_ascii=True, default=str)
                        )
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        current_retry += 1
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer(
                        '',
                        current_step + 1 >= config.MAX_STEPS,
                        token_usage,
                        time_usage,
                        retry=completion_retries
                    )
                    raise AssertionError(
                        'Streaming completion returned no visible output after retries. '
                        f'step={current_step + 1} retries={current_retry}/{config.COMPLETION_MAX_RETRIES} '
                    )

                current_retry = 0
                current_step += 1
                # action step
                if tool_calls:
                    has_search_tool = False

                    memory += {
                        'role': 'assistant',
                        'tool_calls': tool_calls
                    }
                    tool_executor.thought_process.add_tool_calls_group()
                    if completion_retries:
                        tool_executor.thought_process.set_last_group_completion_retry(completion_retries)
                    tool_calls_num = len(tool_calls)
                    for tool_call_id, tool_call in enumerate(tool_calls):
                        tool_name = tool_call['function']['name']
                        if tool_name in ('documents_search', 'issues_search'):
                            has_search_tool = True
                        yield from StreamingCompletionUtils.execute_tool_with_retry(
                            tool_executor,
                            tool_call,
                            ChatCallBacker,
                            memory,
                            tool_call_id,
                            tool_calls_num,
                        )
                    tool_executor.thought_process.update_last_group_tokens_usage(token_usage)
                    tool_executor.thought_process.set_last_group_time_usage(time.time() - time_begin)
                    completion_retries = []

                    found_records = len(tool_executor.cache.get('sources_results', []))
                    if has_search_tool and found_records:
                        yield SSE.search_found(found_records)

                # final answer
                else:
                    tool_executor.thought_process.set_final_answer(
                        content,
                        current_step >= config.MAX_STEPS,
                        token_usage,
                        time.time() - time_begin,
                        retry=completion_retries if completion_retries else None
                    )
                    completion_retries = []

                    # filter invalid references
                    answer, sources = get_answer_and_sources(tool_executor, content)

                    if context.get('is_external_portal'):
                        answer = remove_reference_markers(answer)
                        sources = []

                    thought_process = {} if context.get('is_external_portal') else tool_executor.thought_process.details
                    yield SSE.results(answer, remove_sources_content_and_snippets(sources), thought_process)
                    yield SSE.done()
                    break
        except Exception as e:
            logger.exception(f'Streaming run AI failure: {e}')
            error_msg = repr(e)
            try:
                thought_process = {} if context.get('is_external_portal') else tool_executor.thought_process.details
                yield SSE.results('There is an issue with the AI server or web server (LLM or internal server error), please try again later', [], thought_process)
            except:
                yield SSE.error(error_msg)
            yield SSE.done()
