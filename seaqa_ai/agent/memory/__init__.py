from seaqa_ai.db.models import ChatMessages
from seaqa_ai.agent.memory.openai_memory import OpenAIMemory
from seaqa_ai.agent.memory.smolagents_memory import SmolAgentsMemory
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP
from sqlalchemy import desc
from datetime import datetime, timedelta

def build_memory_from_db(memory_type, db_session_class, system_prompt, session_id, window_limit, valid_time):
    if memory_type == ASK_MODE_PROPERTIES_MAP.MEMORY.value:
        Memory = OpenAIMemory
    elif memory_type == AGENT_MODE_PROPERTIES_MAP.MEMORY.value:
        Memory = SmolAgentsMemory
    else:
        raise ValueError(f'Unknown memory_type: {memory_type}')

    memory = Memory(system_prompt)
    if not session_id:
        return memory

    # context config check
    assert isinstance(window_limit, int), 'Invalid window number of context'
    assert isinstance(valid_time, int), 'Invalid valid time of context'

    # query records for newest
    with db_session_class() as db_session:
        query_args = [
            ChatMessages.session_id == session_id
        ]
        if valid_time > 0:
            query_args.append(
                ChatMessages.created_at > datetime.now() - timedelta(hours=valid_time)
            )
        records = (
            db_session.query(ChatMessages)
            .filter(*query_args)
            .order_by(desc(ChatMessages.created_at))
        )

        if window_limit > 0:
            records = records.limit(window_limit)

        records = records.all()

        records.sort(key=lambda x: x.created_at)
        
        for record in records:
            memory += record.to_dict()

    return memory
