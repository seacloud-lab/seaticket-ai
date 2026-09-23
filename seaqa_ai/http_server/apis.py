import logging
import jwt
import json
from flask import Flask, request, Response, stream_with_context
from seaqa_ai import config
from seaqa_ai.utils import chunk_text, gen_record_hash
from seaqa_ai.services.chat_service import StreamingChat, SyncChat
from seaqa_ai.skills.loader import (
    load_builtin_skill_summaries_map,
    load_builtin_skill,
    load_builtin_skill_content,
    serialize_skill_summary,
    serialize_skill_spec,
)
from seaqa_ai.utils import chunk_text
from seaqa_ai.utils.constants import AIScenario
from seaqa_ai.services.chat_service import SyncChat, StreamingChat

logger = logging.getLogger(__name__)
flask_app = Flask(__name__)

def check_auth_token(req):
    auth = req.headers.get('Authorization', '').split()
    if not auth or auth[0].lower() != 'token' or len(auth) != 2:
        return False

    token = auth[1]
    if not token:
        return False

    private_key = config.JWT_PRIVATE_KEY
    try:
        jwt.decode(token, private_key, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidSignatureError) as e:
        return False

    return True


@flask_app.route('/internal/skills/builtins', methods=['GET'])
def list_builtin_skills_api():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        skills = [
            serialize_skill_summary(skill)
            for skill in load_builtin_skill_summaries_map().values()
        ]
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

    return {'skills': skills}, 200


@flask_app.route('/internal/skills/builtins/<skill_name>', methods=['GET'])
def get_builtin_skill_api(skill_name):
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        skill = load_builtin_skill(skill_name)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

    if not skill:
        return {'error_msg': 'Skill not found.'}, 404
    content = load_builtin_skill_content(skill_name)
    return {'skill': serialize_skill_spec(skill, content)}, 200


# completion API
@flask_app.route('/get-ai-reply', methods=['POST'])
def get_ai_reply():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    message = data.get('query')
    attachments = data.get('attachments', [])
    session_uuid = data.get('session_uuid')
    project_uuid = data.get('project_uuid')
    org_id = data.get('org_id')
    llm_model = data.get('llm_model')
    stream = data.get('stream', False)
    project_prompt = data.get('project_prompt', '')
    is_external_portal = data.get('is_external_portal', False) is True
    portal_chat_prompt = data.get('portal_chat_prompt', '')
    connection_ids = data.get('connection_ids')
    extra_sources = data.get('extra_sources')
    disabled_builtin_skills = data.get('disabled_builtin_skills') or []
    scenario = data.get('scenario', AIScenario.CHAT.value)
    context = {
        'session_uuid': session_uuid,
        'project_uuid': project_uuid,
        'org_id': org_id,
        'project_prompt': project_prompt,
        'is_external_portal': is_external_portal,
        'portal_chat_prompt': portal_chat_prompt,
        'connection_ids': connection_ids,
        'extra_sources': extra_sources,
        'disabled_builtin_skills': disabled_builtin_skills if isinstance(disabled_builtin_skills, list) else [],
        'skip_search_record_hashes': [
            gen_record_hash(attachment.get('type'), attachment.get('connection_id'), attachment.get('record_id'))
            for attachment in attachments
            if isinstance(attachment, dict) and attachment.get('record_id') is not None
        ],
        'scenario': scenario,
    }
    if not is_external_portal:
        username = data.get('username')
        if not username:
            return {'error_msg': 'username invalid.'}, 400
        context['username'] = username

    if not message:
        return {'error_msg': 'question invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400
    if not isinstance(attachments, list):
        return {'error_msg': 'attachments invalid.'}, 400
    
    if stream:
        chat_service = StreamingChat(flask_app.app)
        return Response(
            stream_with_context(chat_service(message, attachments, context, llm_model)),
                content_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
    else:
        chat_service = SyncChat(flask_app.app)
        try:
            return chat_service(message, attachments, context, llm_model), 200
        except Exception as e:
            logger.exception(e)
            return {'error_msg': 'Internet server error.'}, 500

# AI utils
@flask_app.route('/validate-chat-input', methods=['POST'])
def validate_chat_input():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    query = data.get('query')
    attachments = data.get('attachments', [])
    if not isinstance(query, str) or not query.strip():
        return {'error_msg': 'query invalid.'}, 400
    if not isinstance(attachments, list):
        return {'error_msg': 'attachments invalid.'}, 400

    context = {
        'org_id': data.get('org_id'),
        'project_uuid': data.get('project_uuid'),
        'session_uuid': data.get('session_uuid'),
        'project_prompt': data.get('project_prompt', ''),
        'is_external_portal': data.get('is_external_portal', False) is True,
        'portal_chat_prompt': data.get('portal_chat_prompt', ''),
        'scenario': data.get('scenario', AIScenario.CHAT.value),
    }
    try:
        result = flask_app.app.ai_services.validate_chat_input(query, attachments, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return result, 200


@flask_app.route('/generate-chat-title', methods=['POST'])
def generate_chat_title():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    query = data.get('query')
    ai_reply = data.get('ai_reply')
    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')
    session_uuid = data.get('session_uuid')
    scenario = data.get('scenario', AIScenario.CHAT.value)

    if not query:
        return {'error_msg': 'query invalid.'}, 400
    if not ai_reply:
        return {'error_msg': 'ai_reply invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'org_id': org_id,
        'project_uuid': project_uuid,
        'session_uuid': session_uuid,
        'scenario': scenario,
    }

    try:
        title = flask_app.app.ai_services.generate_chat_title(query, ai_reply, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return {'title': title}, 200


@flask_app.route('/convert-record-to-ticket', methods=['POST'])
def convert_record_to_ticket():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    record_detail = data.get('record_detail')
    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.RECORD_GENERATION.value)

    context = {
        'org_id': org_id,
        'project_uuid': project_uuid,
        'scenario': scenario,
    }

    if not record_detail:
        return {'error_msg': 'record_detail invalid.'}, 400

    try:
        draft = flask_app.app.ai_services.gen_ticket_from_record(record_detail, context) or {}
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    tags = draft.get('tags') if isinstance(draft.get('tags'), list) else []
    priority = draft.get('priority')
    if isinstance(priority, bool) or not isinstance(priority, int):
        priority = 0
    return {
        'title': draft.get('title', ''),
        'description': draft.get('description', ''),
        'type': draft.get('type', '') or '',
        'priority': priority,
        'tags': tags,
    }, 200


@flask_app.route('/convert-ticket-to-kb-record', methods=['POST'])
def convert_ticket_to_kb_record():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    ticket_title = data.get('ticket_title')
    ticket_content = data.get('ticket_content')
    ticket_comments = data.get('ticket_comments') or data.get('comments') or []

    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.RECORD_GENERATION.value)

    context = {
        'org_id': org_id,
        'project_uuid': project_uuid,
        'scenario': scenario,
    }

    if not ticket_title and not ticket_content:
        return {'error_msg': 'ticket data invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400
    if ticket_comments and not isinstance(ticket_comments, list):
        return {'error_msg': 'ticket_comments invalid.'}, 400

    try:
        kb_title, kb_content = flask_app.app.ai_services.gen_kb_record_from_ticket(ticket_title or '', ticket_content or '', ticket_comments or [], context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

    return {'kb_title': kb_title, 'kb_content': kb_content}, 200

@flask_app.route('/generate-summary', methods=['POST'])
def generate_summary():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    connection_type = data.get('type')
    content = data.get('content')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.SUMMARY.value)

    if not connection_type:
        return {'error_msg': 'type invalid.'}, 400
    if not content:
        return {'error_msg': 'content invalid'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'project_uuid': project_uuid,
        'scenario': scenario,
    }
    try:
        summary = flask_app.app.ai_services.generate_summary(connection_type, project_uuid, content, context)
        result = {"summary": summary}
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return result, 200


@flask_app.route('/rank-related-records', methods=['POST'])
def rank_records_by_relevance():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    query_record = data.get('query_record')
    candidate_records = data.get('candidate_records')
    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.SEARCH.value)

    if not query_record:
        return {'error_msg': 'query_record invalid.'}, 400
    if not candidate_records:
        return {'error_msg': 'candidate_records invalid.'}, 400
    if not isinstance(candidate_records, list):
        return {'error_msg': 'candidate_records must be a list.'}, 400
    if not org_id:
        return {'error_msg': 'org_id invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'org_id': org_id,
        'project_uuid': project_uuid,
        'scenario': scenario,
    }

    try:
        ranked_ids = flask_app.app.ai_services.rank_records_by_relevance(query_record, candidate_records, context)[0]
        return {'ranked_ids': ranked_ids}, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

# Embedding APIs
@flask_app.route('/embedding/generate', methods=['POST'])
def generate_embedding():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    content = data.get('content')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.UNKNOWN.value)

    if not content:
        return {'error_msg': 'content invalid'}, 400 
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'project_uuid': project_uuid,
        'scenario': scenario,
    }

    try:
        embedding = flask_app.app.embedding_api.generate(content, context)
        return {'embedding': embedding}, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

@flask_app.route('/embedding/generate-by-chunks', methods=['POST'])
def generate_chunks_embedding():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    content = data.get('content')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.SUMMARY.value)

    if not content:
        return {'error_msg': 'content invalid'}, 400 
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'project_uuid': project_uuid,
        'scenario': scenario,
    }
    try:

        chunks = chunk_text(content)
        embeddings = flask_app.app.embedding_api.batch_generate(chunks, context)
        if embeddings and len(embeddings) > 0:
            return {'chunks': chunks, 'embeddings': embeddings}, 200
        return {'error_msg': 'No embedding generated.'}, 400
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

@flask_app.route('/embedding/batch-generate', methods=['POST'])
def batch_generate_embeddings():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    contents = data.get('contents')
    project_uuid = data.get('project_uuid')
    scenario = data.get('scenario', AIScenario.UNKNOWN.value)

    if not contents or not isinstance(contents, list):
        return {'error_msg': 'contents invalid'}, 400 
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'project_uuid': project_uuid,
        'scenario': scenario,
    }

    try:
        embeddings = flask_app.app.embedding_api.batch_generate(contents, context)
        return {'embeddings': embeddings}, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
