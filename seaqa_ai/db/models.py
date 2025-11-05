import json
from sqlalchemy import Column, BigInteger, Enum, String, DateTime, Text, Boolean, UUID
from seaqa_ai.db import Base
from seaqa_ai.utils import localize_time


class ChatMessages(Base):
    __tablename__ = 'chat_messages'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, nullable=False, index=True)
    username = Column(String(length=255), nullable=False)
    role = Column(Enum('user', 'assistant'), nullable=False)
    content = Column(Text, nullable=True)
    sources = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)
    is_agent_mode = Column(Boolean, nullable=False)

    def __init__(self, username, session_id, role, content, sources, created_at, is_agent_mode, updated_at=None):
        self.username = username
        self.session_id = session_id
        self.role = role
        self.content = content
        self.sources = sources
        self.created_at = created_at
        self.updated_at = updated_at
        self.is_agent_mode = is_agent_mode
        

    def to_dict(self):
        try:
            sources = json.loads(self.sources) if self.sources else []
        except:
            sources = self.sources

        res = {
            'id': self.id,
            'username': self.username,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'sources': sources,
            'created_at': localize_time(self.created_at).isoformat(),
            'updated_at': localize_time(self.updated_at).isoformat() if self.updated_at else None,
            'is_agent_mode': self.is_agent_mode
        }
        return res

class ChatToolCalls(Base):
    __tablename__ = 'chat_tool_calls'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_uuid = Column(UUID, nullable=False)
    tool_calls = Column(Text, nullable=False)

    def __init__(self, chat_uuid, tool_calls):
        self.chat_uuid = chat_uuid
        self.tool_calls = json.dumps(tool_calls)

    def to_dict(self):
        try:
            tool_calls = json.loads(self.tool_calls)
        except:
            tool_calls = {}

        res = {
            'id': self.id,
            'chat_uuid': self.chat_uuid,
            'tool_calls': tool_calls
        }
        return res
