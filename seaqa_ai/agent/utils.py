import logging
import json
import datetime
from smolagents import ToolCall
from seaqa_ai.db.models import ChatToolCalls
from seaqa_ai.utils import localize_time, object_to_json_str

logger = logging.getLogger(__name__)

def format_tool_output(output):
    if isinstance(output, dict):
        return {
            'to_observation': output.get('to_observation', output),
            'to_db': output.get('to_db', output),
            'extra_token_usage': output.get('extra_token_usage', {}),
        }
    return {
        'to_observation': output,
        'to_db': output,
        'extra_token_usage': {}
    }

def save_tool_call(db_session_class, session_uuid, message_id, tool_call, output=None, error=None, fix_tool_calls_id=None):
    if isinstance(tool_call, ToolCall):
        tool_call = tool_call.dict()
    
    with db_session_class() as db_session:
        record = db_session.query(ChatToolCalls).filter(ChatToolCalls.session_uuid==session_uuid, ChatToolCalls.message_id==message_id).first()
        
        tool_call_id = tool_call['id'] if not fix_tool_calls_id else fix_tool_calls_id
        arguments = tool_call['function']['arguments']
        tool_call_info = {
            'name': tool_call['function']['name'],
            'arguments': object_to_json_str(arguments),
            'output': output
        }

        try:
            if record:
                new_tool_calls = record.to_dict()['tool_calls'] # dict
                if tool_call_id in new_tool_calls:
                    new_tool_calls[tool_call_id]['substeps'].append(tool_call_info)
                else:
                    new_tool_calls[tool_call_id] = {
                        'substeps': [tool_call_info],
                        'time': localize_time(datetime.datetime.now()).isoformat()
                    }
                if error:
                    if 'error' in new_tool_calls[tool_call_id]:
                        error_type = new_tool_calls[tool_call_id]['error']['type'] + '\n'
                        error_message = new_tool_calls[tool_call_id]['error']['message'] + '\n'
                    else:
                        error_type = ''
                        error_message = ''

                    new_tool_calls[tool_call_id]['error'] = {
                        'type': error_type + type(error).__name__,
                        'message': error_message + repr(error)
                    }
                record.tool_calls = json.dumps(new_tool_calls)
            else:
                new_tool_calls = {
                    'substeps': [tool_call_info],
                    'time': localize_time(datetime.datetime.now()).isoformat()
                }
                if error:
                    new_tool_calls['error'] = {
                        'type': type(error).__name__,
                        'message': repr(error)
                    }
                record = ChatToolCalls(session_uuid, message_id, {
                    tool_call_id: new_tool_calls
                })
                db_session.add(record)
            
            db_session.commit()
        except Exception as e:
            logger.warning(f'Failure to add tool call into db: {e}')
