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
MAX_QA_PER_RUN = 200


def analyze(app, project_uuid, should_stop=None):
    project_uuid_36 = uuid_str_to_36_chars(project_uuid)
    project_uuid_32 = uuid_str_to_32_chars(project_uuid)
    if should_stop and should_stop():
        logger.info('Knowledge gap analysis stopped for project %s', project_uuid_36)
        return
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    limit = MAX_QA_PER_RUN

    with app.db_session_class() as session:
        scope, context, target_language = _load_project_scope(session, project_uuid_32)
        if scope is None:
            logger.warning('Skip knowledge gap analysis, project %s not found', project_uuid_32)
            return
        rows = session.execute(
            text(
                'SELECT m.id, m.session_uuid FROM portal_chat_messages m '
                'INNER JOIN portal_chat_sessions s ON s.session_uuid = m.session_uuid '
                'LEFT JOIN portal_chat_analysis a ON a.session_uuid = m.session_uuid '
                'AND a.message_id = m.message_id '
                'WHERE s.project_uuid = :project_uuid AND m.role = :role '
                'AND m.message_id IS NOT NULL AND m.created_at < :cutoff '
                'AND (a.id IS NULL OR a.evaluated_at IS NULL) '
                'ORDER BY m.created_at DESC LIMIT :limit'
            ),
            {
                'project_uuid': project_uuid_36,
                'role': 'user',
                'cutoff': cutoff,
                'limit': limit,
            },
        ).fetchall()
        turns = _assemble_candidate_turns(session, rows)

    evaluated = 0
    for turn in turns:
        if should_stop and should_stop():
            logger.info(
                'Knowledge gap analysis stopped for project %s, candidates=%s, evaluated=%s',
                project_uuid_36, len(turns), evaluated,
            )
            return
        try:
            if _evaluate_turn(app, turn, scope, context, target_language):
                evaluated += 1
        except Exception:
            logger.exception('Knowledge gap evaluation failed for message %s', turn['id'])
    logger.info(
        'Knowledge gap analysis finished for project %s, candidates=%s, evaluated=%s',
        project_uuid_36, len(turns), evaluated,
    )


def _assemble_candidate_turns(session, candidate_rows):
    if not candidate_rows:
        return []
    candidate_ids = {row[0] for row in candidate_rows}
    session_uuids = {row[1] for row in candidate_rows}
    messages = session.query(PortalChatMessages).filter(
        PortalChatMessages.session_uuid.in_(session_uuids),
        PortalChatMessages.role.in_(('user', 'assistant')),
    ).order_by(
        PortalChatMessages.session_uuid, PortalChatMessages.created_at, PortalChatMessages.id,
    ).all()

    assistants = {}
    for message in messages:
        if message.role != 'assistant' or not message.message_id:
            continue
        key = (message.session_uuid, message.message_id)
        if key not in assistants or message.id > assistants[key].id:
            assistants[key] = message

    # Sliding window to assemble candidate turns. Messages are ordered by session, then 
    # time. `left` stays on the first message of the current session while `right` walks 
    # forward. For a candidate at `right`, the context is the preceding CONTEXT_MESSAGE_LIMIT 
    # messages, clamped so it cannot cross into the previous session.
    turns = []
    left = 0
    for right, message in enumerate(messages):
        if message.session_uuid != messages[left].session_uuid:
            left = right
        if message.id not in candidate_ids:
            continue
        assistant = assistants.get((message.session_uuid, message.message_id))
        earlier = messages[max(left, right - CONTEXT_MESSAGE_LIMIT):right]
        turns.append({
            'id': message.id,
            'session_uuid': message.session_uuid,
            'message_id': message.message_id,
            'created_at': message.created_at,
            'question': message.content or '',
            'answer': assistant.content if assistant else '',
            'sources': _format_sources(assistant.sources if assistant else None),
            'context_text': _format_context(earlier),
        })
    turns.sort(key=lambda turn: (turn['created_at'], turn['id']), reverse=True)
    return turns


def _evaluate_turn(app, turn, scope, usage_context, target_language):
    question = turn['question']
    result = _judge(
        app, usage_context, scope, turn['context_text'], question, turn['answer'],
        turn['sources'], target_language,
    )
    evaluation = result.get('evaluation')
    if evaluation not in KNOWLEDGE_GAP_EVALUATIONS:
        raise ValueError(f'Invalid evaluation: {evaluation}')

    turn_session_uuid = turn['session_uuid']
    turn_message_id = turn['message_id']
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


def _judge(app, usage_context, scope, context_text, question, answer, sources, target_language):
    messages = [
        {'role': 'system', 'content': build_knowledge_gap_system_prompt(target_language)},
        {'role': 'user', 'content': build_knowledge_gap_user_prompt(
            scope, context_text, _clip(question), _clip(answer), sources,
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


def _format_sources(raw):
    if not raw:
        return []
    try:
        sources = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    lines = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        title = source.get('title') or source.get('name') or ''
        if not title:
            continue
        line = str(title)[:200]
        summary = (source.get('ai_summary') or '').strip()
        if summary:
            line = f'{line}\n  Summary: {_clip(summary)}'
        lines.append(line)
    return lines[:10]


def _clip(value):
    value = value or ''
    if len(value) <= MAX_TEXT_CHARS:
        return value
    return value[:MAX_TEXT_CHARS]
