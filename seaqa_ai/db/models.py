import json

from sqlalchemy import Column, BigInteger, Enum, String, DateTime, Text
from seaqa_ai.db import Base


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

    def __init__(self, username, session_id, role, content, sources, created_at, updated_at=None):
        self.username = username
        self.session_id = session_id
        self.role = role
        self.content = content
        self.sources = sources
        self.created_at = created_at
        self.updated_at = updated_at
        

    def to_dict(self):
        try:
            content = json.loads(self.content) if self.content else None
        except json.decoder.JSONDecodeError:
            content = self.content
        try:
            sources = json.loads(self.sources) if self.sources else None
        except json.decoder.JSONDecodeError:
            sources = self.sources
        res = {
            'id': self.id,
            'username': self.username,
            'session_id': self.session_id,
            'role': self.role,
            'content': content,
            'sources': sources,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        return res
