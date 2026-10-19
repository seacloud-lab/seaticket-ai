import json
import logging
from datetime import datetime, timezone

import json_repair
from sqlalchemy import text

from seaqa_ai import config
from seaqa_ai.db.models import PortalChatAnalysis, PortalChatMessages, Project, ProjectConnection, Workspace
from seaqa_ai.utils import gen_completion_format, uuid_str_to_32_chars, uuid_str_to_36_chars
from seaqa_ai.utils.constants import AIScenario, LANGUAGE, MODEL_REASONING_TIER
from seaqa_ai.utils.llm_api import get_completion_client_by_model_tier

from .prompts import build_knowledge_gap_system_prompt, build_knowledge_gap_user_prompt
from .schemas import KNOWLEDGE_GAP_EVALUATIONS, KNOWLEDGE_GAP_SCHEMA

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
CONTEXT_MESSAGE_LIMIT = 4


def analyze(app, project_uuid):
    project_uuid_36 = uuid_str_to_36_chars(project_uuid)
    project_uuid_32 = uuid_str_to_32_chars(project_uuid)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    limit = config.KNOWLEDGE_GAPS_MAX_QA_PER_RUN

    with app.db_session_class() as session:
        scope, context, target_language = _load_project_scope(session, project_uuid_32)
        if scope is None:
            logger.warning('Skip knowledge gap analysis, project %s not found', project_uuid_32)
            return
        rows = session.execute(
            text(
                'SELECT m.id FROM portal_chat_messages m '
                'INNER JOIN portal_chat_sessions s ON s.session_uuid = m.session_uuid '
                'LEFT JOIN portal_chat_analysis a ON a.session_uuid = m.session_uuid '
                'AND a.message_id = m.message_id '
                'WHERE s.project_uuid = :project_uuid AND m.role = :role '
                'AND m.message_id IS NOT NULL AND m.created_at < :cutoff '
                'AND (a.id IS NULL OR a.evaluated_at IS NULL) '
                'ORDER BY m.created_at ASC LIMIT :limit'
            ),
            {
                'project_uuid': project_uuid_36,
                'role': 'user',
                'cutoff': cutoff,
                'limit': limit,
            },
        ).fetchall()
        message_ids = [row[0] for row in rows]

    reviewed = 0
    for message_id in message_ids:
        try:
            if _review_message(app, message_id, scope, context, target_language):
                reviewed += 1
        except Exception:
            logger.exception('Knowledge gap review failed for message %s', message_id)
    logger.info(
        'Knowledge gap analysis finished for project %s, candidates=%s, reviewed=%s',
        project_uuid_36, len(message_ids), reviewed,
    )


def _review_message(app, message_id, scope, usage_context, target_language):
    with app.db_session_class() as session:
        message = session.query(PortalChatMessages).filter(PortalChatMessages.id == message_id).first()
        if not message or not message.message_id:
            return False
        existing = session.query(PortalChatAnalysis).filter(
            PortalChatAnalysis.session_uuid == message.session_uuid,
            PortalChatAnalysis.message_id == message.message_id,
        ).first()
        if existing and existing.evaluated_at is not None:
            return False
        turn_session_uuid = message.session_uuid
        turn_message_id = message.message_id
        assistant = session.query(PortalChatMessages).filter(
            PortalChatMessages.session_uuid == message.session_uuid,
            PortalChatMessages.message_id == message.message_id,
            PortalChatMessages.role == 'assistant',
        ).order_by(PortalChatMessages.id.desc()).first()
        earlier = session.query(PortalChatMessages).filter(
            PortalChatMessages.session_uuid == message.session_uuid,
            PortalChatMessages.created_at < message.created_at,
            PortalChatMessages.role.in_(('user', 'assistant')),
        ).order_by(PortalChatMessages.created_at.desc()).limit(CONTEXT_MESSAGE_LIMIT).all()
        question = message.content or ''
        answer = assistant.content if assistant else ''
        source_titles = _source_titles(assistant.sources if assistant else None)

    context_text = _format_context(list(reversed(earlier)))
    result = _judge(app, usage_context, scope, context_text, question, answer, source_titles, target_language)
    evaluation = result.get('evaluation')
    if evaluation not in KNOWLEDGE_GAP_EVALUATIONS:
        raise ValueError(f'Invalid evaluation: {evaluation}')

    with app.db_session_class() as session:
        analysis = session.query(PortalChatAnalysis).filter(
            PortalChatAnalysis.session_uuid == turn_session_uuid,
            PortalChatAnalysis.message_id == turn_message_id,
        ).first()
        if analysis and analysis.evaluated_at is not None:
            return False
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if not analysis:
            analysis = PortalChatAnalysis(
                session_uuid=turn_session_uuid,
                message_id=turn_message_id,
                created_at=now,
            )
            session.add(analysis)
        analysis.evaluated_at = now
        analysis.evaluation = evaluation
        analysis.evaluation_reason = (result.get('reason') or '').strip() or None
        analysis.updated_at = now
        if evaluation == 'knowledge_gap':
            analysis.normalized_question = (result.get('normalized_question') or '').strip() or question.strip()
            analysis.gap_suggestion = (result.get('suggestion') or '').strip() or None
            analysis.gap_status = 'open'
        session.commit()
    return True


def _judge(app, usage_context, scope, context_text, question, answer, source_titles, target_language):
    messages = [
        {'role': 'system', 'content': build_knowledge_gap_system_prompt(target_language)},
        {'role': 'user', 'content': build_knowledge_gap_user_prompt(
            scope, context_text, _clip(question), _clip(answer), source_titles,
        )},
    ]
    llm_client = get_completion_client_by_model_tier(
        app.data_logger,
        config.AI_UTILS_TIER.get('knowledge_gap_judge', MODEL_REASONING_TIER.LOW.value),
    )
    response = llm_client.completion(
        messages=messages,
        context=usage_context,
        response_format=gen_completion_format(KNOWLEDGE_GAP_SCHEMA, 'knowledge_gap_evaluation'),
    ).choices[0].message.content
    return json_repair.loads(response, strict=True)


def _load_project_scope(session, project_uuid_32):
    project = Project.get_project_by_uuid(session, project_uuid_32)
    if not project:
        return None, None, None
    settings = project.get('settings') or {}
    portal = settings.get('portal') if isinstance(settings.get('portal'), dict) else {}
    allowed = portal.get('chat_allowed_sources') if isinstance(portal.get('chat_allowed_sources'), dict) else {}
    connection_ids = allowed.get('connection_ids') or []
    extra_sources = allowed.get('extra_sources') or []
    connections = ProjectConnection.get_connections_by_ids(session, project_uuid_32, connection_ids)
    metadata = Project.get_project_metadata(session, project_uuid_32)
    org_id = None
    workspace_id = metadata.get('workspace_id')
    if workspace_id:
        org_id = Workspace.get_org_id_by_workspace_id(session, workspace_id)

    source_lines = [f"- {conn['name']} ({conn['type']})" for conn in connections]
    source_lines.extend(f'- {name}' for name in extra_sources if isinstance(name, str) and name)
    scope = '\n'.join([
        f"Project name: {metadata.get('name') or '(unknown)'}",
        f"Project prompt: {settings.get('prompt') or '(none)'}",
        f"Portal chat prompt: {portal.get('chat_prompt') or '(none)'}",
        'Allowed sources:',
        '\n'.join(source_lines) or '(none)',
    ])
    usage_context = {
        'project_uuid': project_uuid_32,
        'org_id': org_id,
        'scenario': AIScenario.ANALYSIS.value,
    }
    target_language = LANGUAGE.get(settings.get('lang') or 'en', 'English')
    return scope, usage_context, target_language


def _format_context(messages):
    lines = []
    for message in messages:
        speaker = 'User' if message.role == 'user' else 'Assistant'
        lines.append(f'{speaker}: {_clip(message.content or "")}')
    return '\n'.join(lines)


def _source_titles(raw):
    if not raw:
        return []
    try:
        sources = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    titles = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        title = source.get('title') or source.get('name') or ''
        if title:
            titles.append(str(title)[:200])
    return titles[:8]


def _clip(value):
    value = value or ''
    if len(value) <= MAX_TEXT_CHARS:
        return value
    return value[:MAX_TEXT_CHARS]
