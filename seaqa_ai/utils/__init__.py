# -*- coding: utf-8 -*-
import logging
import json
import ast
from smolagents.models import MessageRole


logger = logging.getLogger(__name__)

def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass

def format_agent_memory(memory_steps):
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
            'system_prompts': '',
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
        if not results['task']['system_prompts'] and 'model_input_messages' in step:
            """
            step['model_input_messages']: sources messages of inputing (including system_prompts, context and user_input):

            ```json
            [
                // system prompts
                {
                    'role': 'system',
                    'content': [
                        {
                            'type': 'text',
                            'text': 'system_prompts'
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
                    results['task']['system_prompts'] = msg['content'][0]['text']
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
                try:
                    result = json.dumps(ast.literal_eval(result) if isinstance(result, str) else result, indent=4, ensure_ascii=False)
                    result = f"```json\n{result}\n```\n"
                except Exception as e:
                    logger.warning(f'Json decode failure: {e}')

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


class LLMChatCompletionException(Exception):
    pass
