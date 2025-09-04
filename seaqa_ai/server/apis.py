import logging
import jwt
import json

from flask import Flask, request
from seaqa_ai import config
from seaqa_ai.utils.constants import SeasearchType
from urllib.parse import quote

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


@flask_app.route('/generate-answer', methods=['POST'])
def generate_answer():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    query = data.get('query')
    username = data.get('username')
    project_uuid = data.get('project_uuid')
    connection_types = data.get('connection_types')
    connection_ids = data.get('connection_ids')
    resolve_type = data.get('resolve_type', 'ask')

    context = {
        'username': username,
    }

    if not query:
        return {'error_msg': 'question invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400

    if resolve_type == 'agent':
        answer, sources = flask_app.app.text_processing_manager.generate_answer_by_tool_call_agent(query, project_uuid, connection_types, connection_ids)
        return {'answer': answer, 'sources': []}, 200

    try:
        english_keywords, chinese_keywords, query_language = flask_app.app.text_processing_manager.generate_search_keywords(query, context)

        english_results = flask_app.app.seaqa_indexer_api.search(english_keywords, project_uuid, connection_types, connection_ids)

        chinese_results = flask_app.app.seaqa_indexer_api.search(chinese_keywords, project_uuid, connection_types, connection_ids)

        all_results = []
        seen_ids = set()

        if english_results:
            for result in english_results:
                result_id = result.get('_id')
                if result_id and result_id not in seen_ids:
                    seen_ids.add(result_id)
                    all_results.append(result)

        if chinese_results:
            for result in chinese_results:
                result_id = result.get('_id')
                if result_id and result_id not in seen_ids:
                    seen_ids.add(result_id)
                    all_results.append(result)

        search_results = sorted(all_results, key=lambda x: x.get('score', 0), reverse=True)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

    if not search_results:
        return {'answer': 'No relevant information found.', 'sources': []}, 200
    try:
        answer, sources = flask_app.app.text_processing_manager.generate_answer(query, search_results, query_language, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    citation_url = []
    for source in sources:
        type = source.get('type')
        match type:
            case SeasearchType.SITE.value:
                citation_url.append({'title': source.get('url'), 'url': source.get('url')})
            case SeasearchType.SEAFILE.value:
                server_url = source.get('server_url').rstrip('/')
                repo_id = source.get('repo_id')
                path = source.get('path', '')
                filename = source.get('filename', '')

                full_path = path.rstrip('/') + '/' + filename
                encoded_full_path = quote(full_path, safe='/')
                url = f"{server_url}/lib/{repo_id}/file{encoded_full_path}"

                citation_url.append({'title': source.get('filename'), 'url': url})

    return {'answer': answer, 'sources': citation_url}, 200
