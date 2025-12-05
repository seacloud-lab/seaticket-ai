import json
from sqlalchemy import Column, BigInteger, Enum, String, DateTime, Text, Boolean, UniqueConstraint, Index
from seaqa_ai.db import Base
from seaqa_ai.utils import localize_time


class ChatMessages(Base):
    __tablename__ = 'chat_messages'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_uuid = Column(String(length=36), nullable=False)
    message_id = Column(String(length=4), nullable=False)
    username = Column(String(length=255), nullable=False)
    role = Column(Enum('user', 'assistant'), nullable=False)
    content = Column(Text, nullable=True)
    extra_contents = Column(Text, nullable=True)
    sources = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)
    is_agent_mode = Column(Boolean, nullable=False)

    __table_args__ = (
        Index('idx_session_uuid_message_id', 'session_uuid', 'created_at'),
    )

    def __init__(self, username, session_uuid, message_id, role, content, extra_contents, sources, created_at, is_agent_mode, updated_at=None):
        self.username = username
        self.session_uuid = session_uuid
        self.message_id = message_id
        self.role = role
        self.content = content
        self.extra_contents = extra_contents
        self.sources = sources
        self.created_at = created_at
        self.updated_at = updated_at
        self.is_agent_mode = is_agent_mode
        

    def to_dict(self):
        try:
            sources = json.loads(self.sources)
        except:
            sources = self.sources
        
        if not isinstance(sources, list):
            sources = []
        
        try:
            extra_contents = json.loads(self.extra_contents)
        except:
            extra_contents = self.extra_contents

        if not isinstance(extra_contents, list):
            extra_contents = []

        res = {
            'id': self.id,
            'username': self.username,
            'session_uuid': self.session_uuid,
            'message_id': self.message_id,
            'role': self.role,
            'content': self.content,
            'extra_contents': extra_contents,
            'sources': sources,
            'created_at': localize_time(self.created_at).isoformat(),
            'updated_at': localize_time(self.updated_at).isoformat() if self.updated_at else None,
            'is_agent_mode': self.is_agent_mode
        }
        return res

class ChatToolCalls(Base):
    __tablename__ = 'chat_tool_calls'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_uuid = Column(String(length=36), nullable=False)
    message_id = Column(String(length=4), nullable=False)
    tool_calls = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint('session_uuid', 'message_id', name='uniq_session_uuid_message_id'),
    )

    def __init__(self, session_uuid, message_id, tool_calls):
        self.session_uuid = session_uuid
        self.message_id = message_id
        self.tool_calls = json.dumps(tool_calls)

    def to_dict(self):
        try:
            tool_calls = json.loads(self.tool_calls)
        except:
            tool_calls = {}

        res = {
            'id': self.id,
            'session_uuid': self.session_uuid,
            'message_id': str(self.message_id),
            'tool_calls': tool_calls
        }
        return res
