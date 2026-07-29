import logging
import json
import time
from abc import abstractmethod
from collections import deque
from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import ProjectConnection, PortalChatMessages, ChatMessages
from seaqa_ai.utils import uuid_str_to_32_chars, remove_reference_markers, remove_sources_content_and_snippets, get_answer_and_sources
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.llm_api import get_responses_client_by_model_id, STREAM_TEXT_DELTA, STREAM_REASONING_DELTA, STREAM_COMPLETED
from seaqa_ai.utils.sse import SSE
from seaqa_ai.utils.tools import OpenAIToolExecutor, SEARCH_TOOL_NAMES
from seaqa_ai.services.chat_service.utils.chat_utils import BasicChatUtils, StreamingChatUtils
from seaqa_ai.services.chat_service.utils.memory import build_memory_from_db, ChatMemory
from seaqa_ai.utils.tools import TestTool, DocumentsSearch, IssuesSearch, TasksSearch
from seaqa_ai.services.chat_service.tools.content_generators import KnowledgeBaseEntryCreator, MarkdownGenerator, TicketCreator
from seaqa_ai.services.chat_service.system_prompts import MAX_STEPS_DISABLE_TOOL_CALLS_PROMPT
from seaqa_ai.services.chat_service.utils import combine_attachments_to_message, \
    strip_content_details_from_attachments, build_chat_system_prompts, build_user_message_content, \
    extract_skill_command_from_message
from seaqa_ai.services.chat_service.utils.callbacker import ChatCallBacker
from seaqa_ai.services.chat_service.skills.registry import SkillRegistry
from seaqa_ai.services.chat_service.skills.tool import SkillTool

logger = logging.getLogger(__name__)

class BasicChat(object):
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.search_tools = (
            DocumentsSearch(),
            IssuesSearch(),
            TasksSearch(),
        )
        self.content_generators = (
            MarkdownGenerator(),
            KnowledgeBaseEntryCreator(),
            TicketCreator(),
        )
        self.test_tool = TestTool()
        self.skill_tool = SkillTool()

    def _prepare_chat_memory(self, system_prompts, session_uuid, context):
        if session_uuid and not config.DISABLE_CONTEXT:
            try:
                message_model = PortalChatMessages if context.get('is_external_portal') else ChatMessages
                return build_memory_from_db(self.db_session_class, system_prompts, session_uuid, config.CONTEXT_WINDOW_LIMIT, config.CONTEXT_HISTORY_VALID_TIME, message_model=message_model)
            except Exception as e:
                logger.warning(f'Failure to build context: {e}')
        return ChatMemory(system_prompts)

    def _load_connections(self, context):
        project_uuid = uuid_str_to_32_chars(context['project_uuid'])
        connection_ids = context.get('connection_ids')

        doc_types = set(ConnectionType.get_document_connection_types())
        issue_types = set(ConnectionType.get_issue_connection_types())
        task_types = set(ConnectionType.get_task_connection_types())
        if 'document_connections' in context and 'issue_connections' in context and 'task_connections' in context:
            return
        if connection_ids is not None:
            document_connections = []
            issue_connections = []
            task_connections = []
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
                    elif conn['type'] in task_types:
                        task_connections.append(conn)
            context.update({
                'document_connections': document_connections,
                'issue_connections': issue_connections,
                'task_connections': task_connections,
                })
            return

        with self.db_session_class() as db_session:
            context.update({
                'document_connections': ProjectConnection.get_connections_by_project_and_types(db_session, project_uuid, doc_types) if doc_types else [],
                'issue_connections': ProjectConnection.get_connections_by_project_and_types(db_session, project_uuid, issue_types) if issue_types else [],
                'task_connections': ProjectConnection.get_connections_by_project_and_types(db_session, project_uuid, task_types) if task_types else [],
            })

    def _register_tools(self, tool_executor, context):
        self._load_connections(context)
        tool_executor.clean()
        for tool in self.search_tools:
            tool.register(tool_executor, context=context, app=self.app)
        if not context.get('is_external_portal'):
            for tool in self.content_generators:
                tool.register(tool_executor, context=context)
        if config.ENABLE_TEST_TOOL:
            self.test_tool.register(tool_executor)

    def _load_selected_skill(self, tool_executor, memory, context):
        skill_id = context.get('selected_skill_id')
        if not skill_id:
            return

        self.skill_tool.register(tool_executor, context=context)
        tool_call = {
            'type': 'function_call',
            'call_id': f'selected_skill_{skill_id}',
            'name': 'skill',
            'arguments': json.dumps({'name': skill_id}),
        }
        memory += tool_call
        BasicChatUtils.execute_tool_with_retry(tool_executor, tool_call, ChatCallBacker, memory)

    def __call__(self, message, attachments, context, model):
        tool_executor = OpenAIToolExecutor()
        available_skill_ids = [skill.id for skill in SkillRegistry.available_skills(context)]
        skill_id, message = extract_skill_command_from_message(message, available_skill_ids)
        context['selected_skill_id'] = skill_id
        context['skill_id'] = None
        self._register_tools(tool_executor, context)
        return self.run(model, tool_executor, message, attachments, context=context, app=self.app)

    @abstractmethod
    def run(self, model, tool_executor, message, attachments, **kwargs):
        raise NotImplementedError(tool_executor, message, attachments, kwargs)

class SyncChat(BasicChat):
    def run(self, model, tool_executor, message, attachments, **kwargs):
        context = kwargs.get('context', {})
        session_uuid = context.get('session_uuid')
        project_prompt = context.get('project_prompt', '')
        llm_client = get_responses_client_by_model_id(self.app.data_logger, model)

        user_raw_message = combine_attachments_to_message(attachments, message)
        user_message_content = build_user_message_content(attachments, message)

        system_prompts = build_chat_system_prompts(
            project_prompt=project_prompt,
            is_external_portal=context.get('is_external_portal', False),
        )
        memory = self._prepare_chat_memory(system_prompts, session_uuid, context)

        tool_executor.thought_process.set_task(system_prompts, user_raw_message, message, strip_content_details_from_attachments(attachments))
        tool_executor.thought_process.context = memory.context_thought_process

        memory += {
            'role': 'user',
            'content': user_message_content
        }
        self._load_selected_skill(tool_executor, memory, context)

        current_step = 0
        has_inserter_max_step_prompt = False
        while current_step < config.MAX_STEPS:
            current_step += 1
            self._register_tools(tool_executor, context)
            result = None
            llm_message = None
            completion_retries = []
            for current_retry in range(config.COMPLETION_MAX_RETRIES + 1):
                result = None
                llm_message = None
                time_begin = time.time()
                response_token_usage = {}
                try:
                    completion_kwargs, has_inserter_max_step_prompt = BasicChatUtils.build_completion_kwargs(
                        memory, context, tool_executor, current_step, has_inserter_max_step_prompt, MAX_STEPS_DISABLE_TOOL_CALLS_PROMPT
                    )

                    result = llm_client.responses(
                        **completion_kwargs
                    )
                    response_token_usage = result.usage
                    llm_message = result
                    if llm_message.tool_calls and current_step >= config.MAX_STEPS:
                        time_usage = time.time() - time_begin
                        completion_retries.append(BasicChatUtils.build_completion_retry_entry(
                            current_step,
                            current_retry,
                            'tool_calls_at_max_steps',
                            time_usage,
                            token_usage=response_token_usage,
                            error='Invalid response: cannot return tool_calls at max steps'
                        ))
                        logger.warning(
                            'Sync completion returned tool_calls at max steps. model=%s session_uuid=%s project_uuid=%s step=%s retry=%s max_retries=%s max_steps=%s tool_calls=%s',
                            model,
                            session_uuid,
                            context.get('project_uuid'),
                            current_step,
                            current_retry,
                            config.COMPLETION_MAX_RETRIES,
                            config.MAX_STEPS,
                            json.dumps(llm_message.tool_calls, ensure_ascii=True, default=str)
                        )
                        if current_retry < config.COMPLETION_MAX_RETRIES:
                            time.sleep(config.COMPLETION_RETRY_INTERVAL)
                            continue
                        tool_executor.thought_process.set_final_answer('', current_step >= config.MAX_STEPS, response_token_usage, time_usage, retry=completion_retries)
                        raise AssertionError('Invalid response: cannot return tool_calls at max steps')
                    break
                except AssertionError:
                    raise
                except Exception as e:
                    time_usage = time.time() - time_begin
                    completion_retries.append(BasicChatUtils.build_completion_retry_entry(
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

            if llm_message is None:
                raise AssertionError('Invalid AI responses')

            # action step
            if llm_message.tool_calls:
                memory += llm_message.tool_calls
                tool_executor.thought_process.add_tool_calls_group()
                if completion_retries:
                    tool_executor.thought_process.set_last_group_completion_retry(completion_retries)
                for tool_call in llm_message.tool_calls:
                    BasicChatUtils.execute_tool_with_retry(tool_executor, tool_call, ChatCallBacker, memory)
                tool_executor.thought_process.update_last_group_tokens_usage(result.usage)
                tool_executor.thought_process.set_last_group_time_usage(time.time() - time_begin)

            # final answer
            else:
                content = llm_message.content
                tool_executor.thought_process.set_final_answer(
                    content,
                    current_step >= config.MAX_STEPS,
                    result.usage,
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
            llm_client = get_responses_client_by_model_id(self.app.data_logger, model)
            yield SSE.status('Preparing', 'Initializing user input')

            user_raw_message = combine_attachments_to_message(attachments, message)
            user_message_content = build_user_message_content(attachments, message)

            system_prompts = build_chat_system_prompts(
                project_prompt=project_prompt,
                is_external_portal=context.get('is_external_portal', False),
            )
            memory = self._prepare_chat_memory(system_prompts, session_uuid, context)

            tool_executor.thought_process.set_task(system_prompts, user_raw_message, message, strip_content_details_from_attachments(attachments))
            tool_executor.thought_process.context = memory.context_thought_process

            memory += {
                'role': 'user',
                'content': user_message_content
            }
            self._load_selected_skill(tool_executor, memory, context)

            current_step = 0
            current_retry = 0
            completion_retries = []
            has_inserter_max_step_prompt = False
            while current_step < config.MAX_STEPS:
                current_step += 1
                self._register_tools(tool_executor, context)
                detail = f'step {current_step}'
                if current_retry:
                    detail += f', retrying {current_retry}'
                yield SSE.status('LLM Reasoning', detail)
                time_begin = time.time()
                token_usage = {}
                try:
                    completion_kwargs, has_inserter_max_step_prompt = StreamingChatUtils.build_completion_kwargs(
                        memory, context, tool_executor, current_step, has_inserter_max_step_prompt, MAX_STEPS_DISABLE_TOOL_CALLS_PROMPT
                    )
                    response_stream = llm_client.responses_stream(**completion_kwargs)
                except Exception as e:
                    time_usage = time.time() - time_begin
                    completion_retries.append(StreamingChatUtils.build_completion_retry_entry(
                        current_step,
                        current_retry,
                        'completion_error',
                        time_usage,
                        token_usage=token_usage,
                        error=repr(e)
                    ))
                    logger.exception(f'Streaming completion failure at step {current_step}: {e}')
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        current_retry += 1
                        current_step -= 1
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer('', current_step >= config.MAX_STEPS, token_usage, time_usage, retry=completion_retries)
                    raise

                tool_calls = []
                content = ""
                result = None
                has_yield_answering_status = False
                recent_chunks = deque(maxlen=10)

                try:
                    for kind, payload in response_stream:
                        if kind == STREAM_TEXT_DELTA:
                            recent_chunks.append({'text_delta': payload})
                            if not has_yield_answering_status:
                                yield SSE.status('Answering')
                                has_yield_answering_status = True
                            yield SSE.answer(payload)
                            content += payload
                        elif kind == STREAM_REASONING_DELTA:
                            recent_chunks.append({'reasoning_delta': payload})
                        elif kind == STREAM_COMPLETED:
                            result = payload
                            recent_chunks.append({'completed': True})

                    if result is not None:
                        tool_calls = result.tool_calls
                        if result.content:
                            content = result.content
                        if result.usage:
                            token_usage = result.usage
                            llm_client.logger_usage(token_usage, context)
                except Exception as e:
                    if tool_calls or content:
                        raise AssertionError('Cannot deal with invalid response')
                    time_usage = time.time() - time_begin
                    completion_retries.append(StreamingChatUtils.build_completion_retry_entry(
                        current_step,
                        current_retry,
                        'completion_error',
                        time_usage,
                        token_usage=token_usage,
                        error=repr(e)
                    ))
                    logger.exception(f'Streaming completion chunk failure at step {current_step}: {e}')
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        current_retry += 1
                        current_step -= 1
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer('', current_step >= config.MAX_STEPS, token_usage, time_usage, retry=completion_retries)
                    raise

                if not tool_calls and not content:
                    time_usage = time.time() - time_begin
                    completion_retries.append({
                        **StreamingChatUtils.build_completion_retry_entry(
                            current_step,
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
                            current_step,
                            current_retry,
                            config.COMPLETION_MAX_RETRIES,
                            json.dumps(list(recent_chunks), ensure_ascii=True, default=str)
                        )
                    if current_retry < config.COMPLETION_MAX_RETRIES:
                        current_retry += 1
                        current_step -= 1
                        time.sleep(config.COMPLETION_RETRY_INTERVAL)
                        continue
                    tool_executor.thought_process.set_final_answer(
                        '',
                        current_step >= config.MAX_STEPS,
                        token_usage,
                        time_usage,
                        retry=completion_retries
                    )
                    raise AssertionError(
                        'Streaming completion returned no visible output after retries. '
                        f'step={current_step} retries={current_retry}/{config.COMPLETION_MAX_RETRIES} '
                    )

                # action step
                if tool_calls:
                    if current_step >= config.MAX_STEPS:
                        time_usage = time.time() - time_begin
                        completion_retries.append(StreamingChatUtils.build_completion_retry_entry(
                            current_step,
                            current_retry,
                            'tool_calls_at_max_steps',
                            time_usage,
                            token_usage=token_usage,
                            error='Invalid response: cannot return tool_calls at max steps'
                        ))
                        logger.warning(
                            'Streaming completion returned tool_calls at max steps. model=%s session_uuid=%s project_uuid=%s step=%s retry=%s max_retries=%s max_steps=%s tool_calls=%s recent_chunks=%s',
                            model,
                            session_uuid,
                            context.get('project_uuid'),
                            current_step,
                            current_retry,
                            config.COMPLETION_MAX_RETRIES,
                            config.MAX_STEPS,
                            json.dumps(tool_calls, ensure_ascii=True, default=str),
                            json.dumps(list(recent_chunks), ensure_ascii=True, default=str)
                        )
                        if current_retry < config.COMPLETION_MAX_RETRIES:
                            current_retry += 1
                            current_step -= 1
                            time.sleep(config.COMPLETION_RETRY_INTERVAL)
                            continue
                        tool_executor.thought_process.set_final_answer('', current_step >= config.MAX_STEPS, token_usage, time_usage, retry=completion_retries)
                        raise AssertionError('Invalid response: cannot return tool_calls at max steps')
                    current_retry = 0
                    has_search_tool = False

                    memory += tool_calls
                    tool_executor.thought_process.add_tool_calls_group()
                    if completion_retries:
                        tool_executor.thought_process.set_last_group_completion_retry(completion_retries)
                    tool_calls_num = len(tool_calls)
                    for tool_call_id, tool_call in enumerate(tool_calls):
                        tool_name = tool_call.get('name', '')
                        if tool_name in SEARCH_TOOL_NAMES:
                            has_search_tool = True
                        yield from StreamingChatUtils.execute_tool_with_retry(
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
                    current_retry = 0
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
