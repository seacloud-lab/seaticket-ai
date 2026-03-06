import logging
import jwt
import json
from flask import Flask, request, Response, stream_with_context
from seaqa_ai import config
from seaqa_ai.utils import chunk_text
from seaqa_ai.managers.chat_manager import SyncChat, StreamingChat

logger = logging.getLogger(__name__)
flask_app = Flask(__name__)

AGENT_RUNS_TABLE = 'agent_runs'
AGENT_ACTIONS_TABLE = 'agent_actions'

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
    username = data.get('username')
    session_uuid = data.get('session_uuid')
    project_uuid = data.get('project_uuid')
    document_connections = data.get('document_connections', [])
    issue_connections = data.get('issue_connections', [])
    org_id = data.get('org_id')
    llm_model = data.get('llm_model')
    stream = data.get('stream', False)
    context = {
        'username': username,
        'session_uuid': session_uuid,
        'project_uuid': project_uuid,
        'document_connections': document_connections,
        'issue_connections': issue_connections,
        'org_id': org_id
    }

    if not message:
        return {'error_msg': 'question invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not isinstance(attachments, list):
        return {'error_msg': 'attachments invalid.'}, 400
    
    if stream:
        chat_manager = StreamingChat(flask_app.app)
        return Response(
            stream_with_context(chat_manager(message, attachments, context, llm_model)),
                content_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
    else:
        chat_manager = SyncChat(flask_app.app)
        try:
            return chat_manager(message, attachments, context, llm_model), 200
        except Exception as e:
            logger.exception(e)
            return {'error_msg': 'Internet server error.'}, 500

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

    username = data.get('username')
    record_detail = data.get('record_detail')
    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')

    context = {
        'username': username,
        'org_id': org_id,
        'project_uuid': project_uuid
    }

    if not record_detail:
        return {'error_msg': 'record_detail invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400

    try:
        title, description = flask_app.app.ai_utils.gen_ticket_from_record(record_detail, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return {'title': title, 'description': description}, 200


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

    username = data.get('username')
    ticket_title = data.get('ticket_title')
    ticket_content = data.get('ticket_content')
    ticket_comments = data.get('ticket_comments') or data.get('comments') or []

    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')

    context = {
        'username': username,
        'org_id': org_id,
        'project_uuid': project_uuid
    }

    if not ticket_title and not ticket_content:
        return {'error_msg': 'ticket data invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400
    if ticket_comments and not isinstance(ticket_comments, list):
        return {'error_msg': 'ticket_comments invalid.'}, 400

    try:
        kb_title, kb_content = flask_app.app.ai_utils.gen_kb_record_from_ticket(ticket_title or '', ticket_content or '', ticket_comments or [], context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

    return {'kb_title': kb_title, 'kb_content': kb_content}, 200


@flask_app.route('/generate-chunks-embedding', methods=['POST'])
def generate_chunks_embedding():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    username = data.get('username', '')
    content = data.get('content')
    project_uuid = data.get('project_uuid')

    if not content:
        return {'error_msg': 'content invalid'}, 400 
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'username': username,
        'project_uuid': project_uuid
    }
    try:

        chunks = chunk_text(content)
        embeddings = flask_app.app.embedding_api.generate(chunks, context)
        if embeddings and len(embeddings) > 0:
            return {'chunks': chunks, 'embeddings': embeddings}, 200
        return {'error_msg': 'No embedding generated.'}, 400
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500


@flask_app.route('/generate-embedding', methods=['POST'])
def generate_embedding():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    username = data.get('username', '')
    content = data.get('content')
    project_uuid = data.get('project_uuid')

    if not content:
        return {'error_msg': 'content invalid'}, 400 
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'username': username,
        'project_uuid': project_uuid
    }

    try:
        embeddings = flask_app.app.embedding_api.generate(content, context)
        if embeddings and len(embeddings) > 0:
            return {'embedding': embeddings[0].get('embedding')}, 200
        return {'error_msg': 'No embedding generated.'}, 400
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

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
    include_vector = data.get('include_vector', False)
    username = data.get('username', '')
    content = data.get('content')
    project_uuid = data.get('project_uuid')

    if not connection_type:
        return {'error_msg': 'type invalid.'}, 400
    if not content:
        return {'error_msg': 'content invalid'}, 400 
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'username': username,
        'project_uuid': project_uuid
    }
    try:
        summary = flask_app.app.ai_utils.generate_summary(connection_type, project_uuid, content, context)
        result = {"summary": summary, 'embeddings': []}
        if include_vector and summary:
            embeddings = flask_app.app.embedding_api.generate(summary, context)
            if embeddings:
                result["embedding"] = embeddings[0].get('embedding')
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return result, 200


@flask_app.route('/rank-related-issues', methods=['POST'])
def rank_issues_by_relevance():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        return {'error_msg': 'Bad request.'}, 400

    query_record = data.get('query_record')
    candidate_records = data.get('candidate_records')
    username = data.get('username', '')
    org_id = data.get('org_id')
    project_uuid = data.get('project_uuid')

    if not query_record:
        return {'error_msg': 'query_record invalid.'}, 400
    if not candidate_records:
        return {'error_msg': 'candidate_records invalid.'}, 400
    if not isinstance(candidate_records, list):
        return {'error_msg': 'candidate_records must be a list.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not org_id:
        return {'error_msg': 'org_id invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    context = {
        'username': username,
        'org_id': org_id,
        'project_uuid': project_uuid
    }

    try:
        ranked_ids = flask_app.app.ai_utils.rank_issues_by_relevance(query_record, candidate_records, context)[0]
        return {'ranked_ids': ranked_ids}, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

@flask_app.route('/agent/runs/', methods=['GET'])
def list_agent_runs():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    project_uuid = request.args.get('project_uuid')
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    offset = (page - 1) * per_page

    try:
        seadb_api = flask_app.app.seadb_api
        # Get runs
        runs_sql = (
            f"SELECT * FROM `{AGENT_RUNS_TABLE}` "
            f"ORDER BY `started_at` DESC "
            f"LIMIT {offset}, {per_page + 1}"
        )
        runs_result = seadb_api.query_rows(project_uuid, runs_sql)
        runs = runs_result.get('results', [])

        has_more = len(runs) > per_page
        if has_more:
            runs = runs[:per_page]

        # Batch fetch all actions for these runs
        run_ids = [r['_pk'] for r in runs]
        actions_by_run = {}
        if run_ids:
            run_ids_str = ','.join(str(r) for r in run_ids)
            actions_sql = (
                f"SELECT * FROM `{AGENT_ACTIONS_TABLE}` "
                f"WHERE `run_id` IN ({run_ids_str}) "
                f"ORDER BY `run_id` DESC, `created_at` ASC"
            )
            actions_result = seadb_api.query_rows(project_uuid, actions_sql)
            all_actions = actions_result.get('results', [])

            # Group actions by run_id
            for action in all_actions:
                run_id = action.get('run_id')
                if run_id not in actions_by_run:
                    actions_by_run[run_id] = []
                actions_by_run[run_id].append(action)

        # Build enriched runs from pre-grouped actions
        enriched_runs = []
        for run in runs:
            run_pk = run['_pk']
            actions = actions_by_run.get(run_pk, [])

            # Group actions by (source_type, source_id) so each source record
            # appears as one item regardless of how many actions it generated.
            items_map = {}
            for action in actions:
                source_type = action.get('source_type', '')
                source_id = action.get('source_id', '')
                key = (source_type, source_id)
                if key not in items_map:
                    items_map[key] = {
                        'source_type': source_type,
                        'source_id': source_id,
                        'source_title': action.get('source_title', ''),
                        'actions': [],
                    }
                items_map[key]['actions'].append({
                    'id': action['_pk'],
                    'type': action.get('action_type', ''),
                    'tool_name': action.get('tool_name', ''),
                    'content': action.get('content', ''),
                    'result': action.get('result', ''),
                    'status': action.get('status', ''),
                    'suggestion_text': action.get('suggestion_text', ''),
                    'created_at': action.get('created_at', ''),
                    'executed_at': action.get('executed_at', ''),
                })

            enriched_runs.append({
                'id': run_pk,
                'status': run.get('status', ''),
                'started_at': run.get('started_at', ''),
                'finished_at': run.get('finished_at', ''),
                'items_processed': run.get('items_processed', 0),
                'error_message': run.get('error_message', ''),
                'items': list(items_map.values()),
            })

        return {'runs': enriched_runs, 'has_more': has_more}, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internal server error.'}, 500

@flask_app.route('/agent/runs/<int:run_id>/', methods=['GET'])
def get_agent_run_detail(run_id):
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    project_uuid = request.args.get('project_uuid')
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    try:
        seadb_api = flask_app.app.seadb_api
        run_sql = f"SELECT * FROM `{AGENT_RUNS_TABLE}` WHERE `_pk` = {run_id}"
        run_result = seadb_api.query_rows(project_uuid, run_sql)
        runs = run_result.get('results', [])
        if not runs:
            return {'error_msg': 'Run not found.'}, 404
        run = runs[0]

        actions_sql = (
            f"SELECT * FROM `{AGENT_ACTIONS_TABLE}` "
            f"WHERE `run_id` = {run_id} "
            f"ORDER BY `created_at` ASC"
        )
        actions_result = seadb_api.query_rows(project_uuid, actions_sql)
        actions = actions_result.get('results', [])

        items_map = {}
        for action in actions:
            source_type = action.get('source_type', '')
            source_id = action.get('source_id', '')
            key = (source_type, source_id)
            if key not in items_map:
                items_map[key] = {
                    'source_type': source_type,
                    'source_id': source_id,
                    'source_title': action.get('source_title', ''),
                    'actions': [],
                }
            items_map[key]['actions'].append({
                'id': action['_pk'],
                'type': action.get('action_type', ''),
                'tool_name': action.get('tool_name', ''),
                'content': action.get('content', ''),
                'result': action.get('result', ''),
                'status': action.get('status', ''),
                'suggestion_text': action.get('suggestion_text', ''),
                'created_at': action.get('created_at', ''),
                'executed_at': action.get('executed_at', ''),
            })

        return {
            'id': run['_pk'],
            'status': run.get('status', ''),
            'started_at': run.get('started_at', ''),
            'finished_at': run.get('finished_at', ''),
            'items_processed': run.get('items_processed', 0),
            'error_message': run.get('error_message', ''),
            'items': list(items_map.values()),
        }, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internal server error.'}, 500

@flask_app.route('/agent/trigger/', methods=['POST'])
def trigger_agent():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied.'}, 403

    try:
        data = json.loads(request.data)
    except Exception:
        return {'error_msg': 'Bad request.'}, 400

    project_uuid = data.get('project_uuid')
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400

    try:
        agent_manager = flask_app.app.agent_scheduler.agent_manager
        agent_manager.run_single_project(project_uuid)
        return {'success': True}, 200
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internal server error.'}, 500
