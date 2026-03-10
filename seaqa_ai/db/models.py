import json
import logging
from sqlalchemy import Column, BigInteger, Enum, String, DateTime, Text, Boolean, Index, Integer, SmallInteger
from seaqa_ai.db import Base
from seaqa_ai.utils import localize_time
from sqlalchemy import exc as sqla_exc

logger = logging.getLogger(__name__)

class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(length=32), unique=True, nullable=False, index=True)
    name = Column(String(length=255), nullable=False)
    creator = Column(String(length=255), nullable=False)
    modifier = Column(String(length=255), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    workspace_id = Column(Integer, nullable=False)
    deleted = Column(SmallInteger, nullable=False, default=0, index=True)
    delete_time = Column(DateTime, nullable=True)
    color = Column(String(length=50), nullable=True)
    text_color = Column(String(length=50), nullable=True)
    icon = Column(String(length=50), nullable=True)
    settings = Column(Text, nullable=True)
    knowledge_base_indexed_at = Column(DateTime, nullable=True)
    knowledge_base_ai_indexed_at = Column(DateTime, nullable=True)
    ticket_indexed_at = Column(DateTime, nullable=True)
    ticket_ai_indexed_at = Column(DateTime, nullable=True)

    @classmethod
    def get_all_non_deleted_projects(cls, session):
        try:
            projects_query = session.query(cls.uuid, cls.settings).filter(cls.deleted == 0)
            projects = []
            for project in projects_query:
                try:
                    settings = json.loads(project.settings or '{}')
                except (json.JSONDecodeError, TypeError):
                    settings = {}
                projects.append({
                    'uuid': str(project.uuid),
                    'settings': settings,
                })
            return projects
        except Exception as e:
            logger.exception(f'Failed to query projects: {e}')
            return []

    @classmethod
    def get_project_by_uuid(cls, session, uuid: str):
        try:
            project = session.query(cls.uuid, cls.settings).filter(
                cls.uuid == uuid,
                cls.deleted == 0
            ).first()
            
            if not project:
                return None
            
            try:
                settings = json.loads(project.settings or '{}')
            except (json.JSONDecodeError, TypeError):
                settings = {}
            
            return {
                'uuid': str(project.uuid),
                'settings': settings,
            }
        except Exception as e:
            logger.exception(f'Failed to query project by uuid: {e}')
            return None

    @classmethod
    def get_project_metadata(cls, session, uuid: str) -> dict:
        """Query project name and workspace_id by UUID."""
        try:
            project = session.query(cls.name, cls.workspace_id).filter(
                cls.uuid == uuid,
                cls.deleted == 0
            ).first()
            
            if not project:
                return {'name': None, 'workspace_id': None}
            
            return {
                'name': project[0],
                'workspace_id': project[1],
            }
        except Exception as e:
            logger.exception(f'Failed to query project metadata for uuid {uuid}: {e}')
            return {'name': None, 'workspace_id': None}


class ProjectConnection(Base):
    __tablename__ = 'project_connection'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_uuid = Column(String(length=32), nullable=False, index=True)
    name = Column(String(length=255), nullable=False)
    type = Column(String(length=255), nullable=False)
    config = Column(Text, nullable=False)
    modifier = Column(String(length=255), nullable=False)
    created_at = Column(DateTime, nullable=False, index=True)
    last_sync_time = Column(DateTime, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    content_vector_indexed_at = Column(DateTime, nullable=True)
    status = Column(Text, nullable=False)
    deleted = Column(SmallInteger, nullable=False, default=0, index=True)
    is_active = Column(SmallInteger, nullable=False, default=1, index=True)
    last_sync_log = Column(Text, nullable=True)
    ai_status = Column(Text, nullable=True)
    last_ai_processing_time = Column(DateTime, nullable=True)
    ai_indexed_at = Column(DateTime, nullable=True)
    content_vector_status = Column(Text, nullable=True)

    @classmethod
    def get_connections_by_project_and_type(cls, session, project_uuid: str, connection_type: str) -> list[dict]:
        try:
            connections_query = session.query(cls.id, cls.type, cls.name).filter(
                cls.project_uuid == project_uuid,
                cls.type == connection_type,
                cls.deleted == 0
            )
            return [
                {'id': conn[0], 'type': conn[1], 'name': conn[2]}
                for conn in connections_query
            ]
        except Exception as e:
            logger.exception(f'Failed to query connections for project {project_uuid}: {e}')
            return []

    @classmethod
    def get_connection_ids_by_project_and_types(cls, session, project_uuid: str, connection_types: tuple) -> list[int]:
        """Query active connection IDs for a project filtered by multiple types."""
        try:
            connections_query = session.query(cls.id).filter(
                cls.project_uuid == project_uuid,
                cls.type.in_(connection_types),
                cls.deleted == 0,
                cls.is_active == 1
            )
            return [conn[0] for conn in connections_query]
        except Exception as e:
            logger.exception(f'Failed to query connection ids for project {project_uuid}: {e}')
            return []


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
    as_context = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index('idx_session_uuid_created_at', 'session_uuid', 'created_at'),
        Index('idx_session_uuid_created_at_as_context_role', 'session_uuid', 'role', created_at.desc(), as_context.desc())
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
            'as_context': self.as_context
        }
        return res
