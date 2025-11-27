# -*- coding: utf-8 -*-
import logging
import json
import pytz
import json_repair
from datetime import datetime
from smolagents.models import MessageRole
from smolagents.agent_types import AgentText
from seaqa_ai.config import TIME_ZONE

time_zone = pytz.timezone(TIME_ZONE)
logger = logging.getLogger(__name__)

def localize_time(date):
    """
    Transfer non-time-zone date to time-zone date

    date: datetime object
    """
    if isinstance(date, datetime):
        if date.tzinfo is None:
            date = pytz.utc.localize(date)
        return date.astimezone(time_zone)
    return date

def clean_json_markdown(raw_response):
    if raw_response.strip().startswith('```'):
        lines = raw_response.strip().splitlines()
        if lines[0].startswith("```") and lines[-1] == "```":
            return "\n".join(lines[1:-1])
    return raw_response

def parse_json(json_str):
    """
    Parse a JSON string OR AgentText into a JSON object with error correction
    """
    if isinstance(json_str, str) or isinstance(json_str, AgentText):
        try:
            json_obj = json_repair.loads(json_str)
        except Exception as e:
            logger.warning(f'Failure to parse to json {e}')
            return json_str
        return json_obj
    return json_str

def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass

def extract_sources(tool_executor, document_indexes):
    sources_results = tool_executor.tool_shared_vars.get('sources_results', [])
    sources = []
    for index in document_indexes:
        if index < len(sources_results):
            sources.append(sources_results[index])
    return sources

def object_to_json_str(obj):
    result = obj
    if isinstance(obj, str) or isinstance(obj, AgentText):
        result = parse_json(obj)
    result = convert_to_json_code_block(result)
    return result

def convert_to_json_code_block(obj):
    if not isinstance(obj, str) and not isinstance(obj, AgentText):
        result = '```json\n' + json.dumps(obj, indent=4, ensure_ascii=False) + '\n```'
        return result
    return obj

def smolagent_steps_to_thought_process(memory_steps):
    """
    # Format output of agent memory_steps

    ## Args
    - memory_steps: steps of SmolAgents memory

    ## Returns
    Json-like object with:
    - task: system prompts & user input
    - context: chat history
    - actions: Action steps with tokens usage
    - final_answer: final answer & with tokens usage
    """

    actions_input_tokens = 0
    actions_output_tokens = 0
    actions_total_tokens = 0
    actions_time_usage = 0

    answer_input_tokens = 0
    answer_output_tokens = 0
    answer_total_tokens = 0
    answer_time_usage = 0

    results = {
        'task': {
            'system_prompt': '',
            'user_input': ''
        },
        'context': [],
        'actions': [],
        'final_answer': {
            'result': '',
            'token_usage': {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            },
            'time_usage': 0
        },
        'static': {
            'token_usage': {
                'input_tokens': {
                    'action_steps': 0,
                    'answer_generation': 0,
                    'total': 0
                },
                'output_tokens': {
                    'action_steps': 0,
                    'answer_generation': 0,
                    'total': 0
                },
                'total_tokens': {
                    'action_steps': 0,
                    'answer_generation': 0,
                    'total': 0
                }
            },
            'time_usage': {
                'action_steps': 0,
                'answer_generation': 0,
                'total': 0
            }
        }
    }

    for step in memory_steps:
        # task
        if not results['task']['user_input'] and 'task' in step:
            results['task']['user_input'] = step['task']
        if not results['task']['system_prompt'] and 'model_input_messages' in step:
            """
            step['model_input_messages']: sources messages of inputing (including system_prompt, context and user_input):

            ```json
            [
                // system prompts
                {
                    'role': 'system',
                    'content': [
                        {
                            'type': 'text',
                            'text': 'system_prompt'
                        }
                    ]
                },

                // context
                {
                    'role': 'user' // or 'assistant'
                    'content': [
                        {
                            'type': 'text',
                            'text': 'a history chat message'
                        }
                    ]
                },

                // user input
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': 'user input'
                        }
                    ]
                }
            ]
            ```
            """
            
            for msg in step['model_input_messages']:
                if msg.get('role') == MessageRole.SYSTEM:
                    results['task']['system_prompt'] = msg['content'][0]['text']
                    break

        # context    
        if 'context' in step:
            """
            response['context'] = [
                {
                    'date': 'date when user sent the message', #str
                    'user_input': 'user_input', # str
                    'assistant_response': [
                        'response_1', # -> {'answer', 'sources'}
                        'response_2',
                        # ...
                    ]
                },
                # ...
            ]
            """
            if step['context']['role'] == 'assistant':
                if not results['context']:
                    results['context'].append({
                        'date': step['context']['date'],
                        'user_input': '',
                        'assistant_response': [{
                            'date': step['context']['date'],
                            'content': step['context']['content']
                        }]
                    })
                else:
                    results['context'][-1]['assistant_response'].append({
                        'date': step['context']['date'],
                        'content': step['context']['content']
                    })
            else:
                results['context'].append({
                    'date': step['context']['date'],
                    'user_input': step['context']['content'],
                    'assistant_response': []
                })

        if (tool_calls := step.get('tool_calls')) or (error := step.get('error', {})):
            # action step

            if not step['is_final_answer'] and not error.get('type') == 'AgentMaxStepsError':
                # common actions

                tks = [{
                        'name': tool_call['function']['name'],
                        'arguments': tool_call['function']['arguments'],
                    }
                    for tool_call in tool_calls
                ]

                actions_input_tokens += step['token_usage']['input_tokens']
                actions_output_tokens += step['token_usage']['output_tokens']
                actions_total_tokens += step['token_usage']['total_tokens']
                actions_time_usage += step['timing']['duration']

                results['actions'].append({
                    'tool_calls': tks,
                    'token_usage': {
                        'input_tokens': step['token_usage']['input_tokens'],
                        'output_tokens': step['token_usage']['output_tokens'],
                        'total_tokens': step['token_usage']['total_tokens'],
                    },
                    'time_usage': step['timing']['duration'],
                    'result': step['action_output'] if error else step['observations']
                })
                if error:
                    results['actions'][-1]['error'] = {
                        'type': error['type'],
                        'message': error['message']
                    }

            else:
                # final answer
                result = step['action_output'] if error else step['observations']
                result = object_to_json_str(result)

                answer_input_tokens = step['token_usage']['input_tokens']
                answer_output_tokens = step['token_usage']['output_tokens']
                answer_total_tokens = step['token_usage']['total_tokens']
                answer_time_usage = step['timing']['duration']

                results['final_answer'] = {
                    'result': result,
                    'token_usage': {
                        'input_tokens': step['token_usage']['input_tokens'],
                        'output_tokens': step['token_usage']['output_tokens'],
                        'total_tokens': step['token_usage']['total_tokens'],
                    },
                    'time_usage': step['timing']['duration'],
                    'reach_max_steps': True if error else False
                }
    
    results['static'] = {
        'token_usage': {
            'input_tokens': {
                'action_steps': actions_input_tokens,
                'answer_generation': answer_input_tokens,
                'total': actions_input_tokens + answer_input_tokens
            },
            'output_tokens': {
                'action_steps': actions_output_tokens,
                'answer_generation': answer_output_tokens,
                'total': actions_output_tokens + answer_output_tokens
            },
            'total_tokens': {
                'action_steps': actions_total_tokens,
                'answer_generation': answer_total_tokens,
                'total': actions_total_tokens + answer_total_tokens
            }
        },
        'time_usage': {
            'action_steps': actions_time_usage,
            'answer_generation': answer_time_usage,
            'total': actions_time_usage + answer_time_usage
        }
    }

    return results


def chunk_text(text, max_tokens=500, overlap_tokens=120):
    if not text:
        return []
    
    import tiktoken
    try:
        encoding = tiktoken.encoding_for_model("text-embedding-3-small")
    except Exception:
        encoding = tiktoken.get_encoding("cl100k_base")
    
    tokens = encoding.encode(text)
    n = len(tokens)
    stride = max_tokens - overlap_tokens
    
    if stride <= 0:
        raise ValueError("overlap_tokens must be less than max_tokens to avoid infinite loop.")
    
    chunks = []
    start = 0
    while start < n:
        end = min(start + max_tokens, n)
        chunk_tokens = tokens[start:end]
        chunks.append(encoding.decode(chunk_tokens))
        if end == n:
            break
        start += stride
    
    return chunks

class LLMChatCompletionException(Exception):
    pass
