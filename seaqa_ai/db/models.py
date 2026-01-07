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
    attachments = Column(Text, nullable=True)
    sources = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)
    is_agent_mode = Column(Boolean, nullable=False)

    __table_args__ = (
        Index('idx_session_uuid_message_id', 'session_uuid', 'created_at'),
    )   

    def to_dict(self):
        try:
            sources = json.loads(self.sources) if self.sources else []
            sources = json.loads(self.sources)
        except:
            sources = self.sources
        
        if not isinstance(sources, list):
            sources = []
        
        try:
            attachments = json.loads(self.attachments)
        except:
            attachments = self.attachments

        if not isinstance(attachments, list):
            attachments = []

        res = {
            'id': self.id,
            'username': self.username,
            'session_uuid': self.session_uuid,
            'message_id': self.message_id,
            'role': self.role,
            'content': self.content,
            'attachments': attachments,
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
