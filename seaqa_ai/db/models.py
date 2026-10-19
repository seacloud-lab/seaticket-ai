import json
import logging
from sqlalchemy import Column, BigInteger, Enum, String, DateTime, Text, Boolean, Index, Integer, SmallInteger
from seaqa_ai.db import Base
from seaqa_ai.utils import localize_time
from seaqa_ai.utils.constants import merge_project_agent_settings_defaults
from datetime import datetime, timezone, timedelta

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
    last_ticket_active_time = Column(DateTime, nullable=True)
    last_agent_scanned_at = Column(DateTime, nullable=True)

    @classmethod
    def get_all_active_projects(cls, session, page: int = 1, page_size: int = 50):
        try:
            now = datetime.now(timezone.utc)
            active_threshold = now - timedelta(days=60)
            base_query = session.query(cls.uuid, cls.settings, cls.last_agent_scanned_at).filter(
                cls.deleted == 0, 
                cls.last_ticket_active_time > active_threshold
            )
            offset = (page - 1) * page_size
            projects_query = base_query.offset(offset).limit(page_size)
            projects = []
            for project in projects_query:
                try:
                    settings = json.loads(project.settings or '{}')
                except (json.JSONDecodeError, TypeError):
                    settings = {}
                settings = merge_project_agent_settings_defaults(settings)
                projects.append({
                    'uuid': str(project.uuid),
                    'settings': settings,
                    'last_agent_scanned_at': project.last_agent_scanned_at,
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
            settings = merge_project_agent_settings_defaults(settings)
            
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

    @classmethod
    def update_last_agent_scanned_at(cls, session, uuid: str) -> bool:
        try:
            session.query(cls).filter(cls.uuid == uuid).update(
                {cls.last_agent_scanned_at: datetime.now(timezone.utc)},
                synchronize_session=False,
            )
            session.commit()
            return True
        except Exception as e:
            logger.exception(f'Failed to update last_agent_scanned_at for uuid {uuid}: {e}')
            session.rollback()
            return False

class Workspace(Base):
    __tablename__ = 'workspaces'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(length=255), nullable=True)
    owner = Column(String(length=255), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False)
    org_id = Column(Integer, nullable=False, index=True)
    deleted = Column(SmallInteger, nullable=False, default=0, index=True)
    delete_time = Column(DateTime, nullable=True)

    @classmethod
    def get_org_id_by_workspace_id(cls, session, workspace_id: int):
        try:
            workspace = session.query(cls.org_id).filter(
                cls.id == workspace_id,
                cls.deleted == 0
            ).first()
            return workspace[0] if workspace else None
        except Exception as e:
            logger.exception(f'Failed to query workspace org_id by workspace_id {workspace_id}: {e}')
            return None


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
                cls.deleted == 0,
                cls.is_active == 1
            )
            return [
                {'id': conn[0], 'type': conn[1], 'name': conn[2]}
                for conn in connections_query
            ]
        except Exception as e:
            logger.exception(f'Failed to query connections for project {project_uuid}: {e}')
            return []
        
    @classmethod
    def get_connections_by_project_and_types(cls, session, project_uuid: str, connection_types: str) -> list[dict]:
        try:
            connections_query = session.query(cls.id, cls.type, cls.name).filter(
                cls.project_uuid == project_uuid,
                cls.type.in_(connection_types),
                cls.deleted == 0,
                cls.is_active == 1
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

    @classmethod
    def get_connections_by_ids(cls, session, project_uuid: str, connection_ids: list) -> list[dict]:
        """Query active connections for a project filtered by specific IDs."""
        if not connection_ids:
            return []
        try:
            connections_query = session.query(cls.id, cls.type, cls.name).filter(
                cls.project_uuid == project_uuid,
                cls.id.in_(connection_ids),
                cls.deleted == 0,
                cls.is_active == 1,
            )
            return [
                {'id': conn[0], 'type': conn[1], 'name': conn[2]}
                for conn in connections_query
            ]
        except Exception as e:
            logger.exception(f'Failed to query connections by ids for project {project_uuid}: {e}')
            return []

class ProfileProfile(Base):
    __tablename__ = 'profile_profile'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String(length=254), unique=True, nullable=False, index=True)
    nickname = Column(String(length=64), nullable=False)
    intro = Column(Text, nullable=False)
    lang_code = Column(Text, nullable=True)
    login_id = Column(String(length=225), nullable=True, unique=True, index=True)
    contact_email = Column(String(length=225), nullable=True, unique=True, index=True)
    institution = Column(String(length=225), nullable=True, index=True)
    list_in_address_book = Column(SmallInteger, nullable=False, default=1)
    phone = Column(String(length=20), nullable=True, unique=True)
    need_show_video = Column(SmallInteger, nullable=False, default=0)
    unit = Column(Text, nullable=True)
    sms_2fa = Column(SmallInteger, nullable=False, default=0)
    is_manually_set_contact_email = Column(SmallInteger, nullable=False, default=0)

    @classmethod
    def get_profiles_by_users(cls, session, users: list) -> list[dict]:
        try:
            profiles_query = session.query(cls.user, cls.nickname, cls.contact_email).filter(
                cls.user.in_(users)
            )
            return [
                {
                    'user': profile[0],
                    'nickname': profile[1],
                    'contact_email': profile[2]
                }
                for profile in profiles_query
            ]
        except Exception as e:
            logger.exception(f'Failed to query profiles for users: {e}')
            return []

class ChatMessages(Base):
    __tablename__ = 'chat_messages'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_uuid = Column(String(length=36), nullable=False, index=True)
    message_id = Column(String(length=4), nullable=False)
    role = Column(Enum('user', 'assistant'), nullable=False)
    content = Column(Text, nullable=True)
    attachments = Column(Text, nullable=True)
    sources = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)
    as_context = Column(Boolean, nullable=False, default=True)

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


class PortalChatMessages(Base):
    __tablename__ = 'portal_chat_messages'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_uuid = Column(String(length=36), nullable=False)
    message_id = Column(String(length=4), nullable=True)
    role = Column(String(length=20), nullable=False)
    content = Column(Text, nullable=True)
    sources = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)
    as_context = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index('idx_portal_chat_msg_session_created', 'session_uuid', 'created_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'session_uuid': self.session_uuid,
            'message_id': self.message_id,
            'role': self.role,
            'content': self.content,
            'attachments': [],
            'sources': [],
            'created_at': localize_time(self.created_at).isoformat(),
            'updated_at': localize_time(self.updated_at).isoformat() if self.updated_at else None,
            'as_context': self.as_context
        }


class PortalChatAnalysis(Base):
    __tablename__ = 'portal_chat_analysis'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_uuid = Column(String(length=36), nullable=False)
    message_id = Column(String(length=4), nullable=False)
    evaluated_at = Column(DateTime, nullable=True)
    evaluation = Column(String(length=20), nullable=True)
    evaluation_reason = Column(Text, nullable=True)
    normalized_question = Column(Text, nullable=True)
    gap_suggestion = Column(Text, nullable=True)
    gap_status = Column(String(length=16), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('uniq_portal_chat_analysis_turn', 'session_uuid', 'message_id', unique=True),
        Index('idx_portal_chat_analysis_evaluation', 'evaluation', 'gap_status'),
    )
