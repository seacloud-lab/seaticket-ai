import logging
import json
from smolagents import ToolCall
from seaqa_ai.db.models import ChatToolCalls

logger = logging.getLogger(__name__)

def tool_output(output):
    if isinstance(output, dict):
        return output.get('to_observation', output), output.get('to_db', output)
    return output, output

def save_tool_call(db_session_class, session_uuid, message_id, tool_call, output):
    """
    Mainly used in SomlAgents.ToolCallingAgent
    """
    if isinstance(tool_call, ToolCall):
        tool_call = tool_call.dict()
    
    with db_session_class() as db_session:
        record = db_session.query(ChatToolCalls).filter(ChatToolCalls.session_uuid==session_uuid, ChatToolCalls.message_id==message_id).first()
        
        tool_call_id = tool_call['id']
        tool_call_info = {
            'name': tool_call['function']['name'],
            'arguments': tool_call['function']['arguments'],
            'output': output
        }

        try:
            if record:
                new_tool_calls = record.to_dict()['tool_calls'] # list
                if tool_call_id in new_tool_calls:
                    new_tool_calls[tool_call_id]['substeps'].append(tool_call_info)
                else:
                    new_tool_calls[tool_call_id] = {
                        'substeps': [tool_call_info]
                    }
                record.tool_calls = json.dumps(new_tool_calls)
            else:
                record = ChatToolCalls(session_uuid, message_id, {
                    tool_call_id: {
                        'substeps': [tool_call_info]
                    }
                })
                db_session.add(record)
            
            db_session.commit()
        except Exception as e:
            logger.warning(f'Failure to add tool call into db: {e}')

