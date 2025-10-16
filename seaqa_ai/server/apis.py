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
    
    results = flask_app.app.chat_manager.generate_answer(resolve_type, query, project_uuid, connection_ids, context)

    try:
        # agent mode
        answer, agent_memory, sources = results
        return {'answer': answer, 'agent_memory': agent_memory, 'sources': sources}, 200
    except:
        # ask mode
        try:
            answer, sources = results
            return {'answer': answer, 'agent_memory': [], 'sources': sources}, 200

        except:
            return {'error_msg': 'Internet server error.'}, 500


@flask_app.route('/create-ticket-info', methods=['POST'])
def create_ticket_info():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403

    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400

    connection_id = data.get('connection_id')
    username = data.get('username')
    record_id = data.get('record_id')

    context = {
        'username': username,
    }

    if not connection_id:
        return {'error_msg': 'connection_id invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not record_id:
        return {'error_msg': 'record_id invalid.'}, 400
    try:
        title, description = flask_app.app.chat_manager.create_ticket_info(connection_id, record_id, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return {'title': title, 'description': description}, 200
