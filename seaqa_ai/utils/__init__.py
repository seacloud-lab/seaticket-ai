# -*- coding: utf-8 -*-
import logging
import json
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
        }
    }

    for step in memory_steps:
        # task
        if not results['task']['user_input'] and 'task' in step:
            results['task']['user_input'] = step['task']
        if not results['task']['system_prompts'] and 'model_input_messages' in step:
            results['task']['system_prompts'] = next(
                (msg['content'][0]['text'] for msg in step['model_input_messages'] 
                if msg.get('role') == MessageRole.SYSTEM)
            )

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

        if tool_calls := step.get('tool_calls'):
            # action step
            error = step.get('error') or {}

            if not step['is_final_answer'] and not error.get('type') == 'AgentMaxStepsError':
                # common actions

                tks = [{
                        'name': tool_call['function']['name'],
                        'arguments': tool_call['function']['arguments'],
                    }
                    for tool_call in tool_calls
                ]

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
                result: str = step['action_output'] if error else step['observations']
                result = result.replace('\'', '"')
                try:
                    result = json.dumps(json.loads(result), indent=4, sort_keys=True, ensure_ascii=False)
                    result = f"```json\n{result}\n```\n"
                except Exception as e:
                    logger.warning(f'Json decode failure: {e}')
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
    
    return results


class LLMChatCompletionException(Exception):
    pass
