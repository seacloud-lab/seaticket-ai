import logging
import jwt
import json

from flask import Flask, request
from seaqa_ai import config
from seaqa_ai.utils.constants import SeasearchType, ASK_MODE_PROPERTIES_MAP
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

    query = data.get('query')
    username = data.get('username')
    session_id = data.get('session_id')
    project_uuid = data.get('project_uuid')
    connection_ids = data.get('connection_ids')
    resolve_type = data.get('resolve_type', ASK_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value)

    context = {
        'username': username,
        'session_id': session_id
    }

    if not query:
        return {'error_msg': 'question invalid.'}, 400
    if not project_uuid:
        return {'error_msg': 'project_uuid invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    
    results = flask_app.app.chat_manager.get_ai_reply(resolve_type, query, project_uuid, connection_ids, context)

    try:
        # agent mode
        ai_reply, agent_memory, sources = results
        return {'ai_reply': ai_reply, 'agent_memory': agent_memory, 'sources': sources}, 200
    except:
        # ask mode
        try:
            ai_reply, sources = results
            return {'ai_reply': ai_reply, 'agent_memory': [], 'sources': sources}, 200

        except:
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

    context = {
        'username': username,
    }

    if not record_detail:
        return {'error_msg': 'record_detail invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400

    try:
        title, description = flask_app.app.chat_manager.gen_ticket_from_record(record_detail, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return {'title': title, 'description': description}, 200


@flask_app.route('/generate-title', methods=['POST'])
def generate_title():
    is_valid = check_auth_token(request)
    if not is_valid:
        return {'error_msg': 'Permission denied'}, 403
    try:
        data = json.loads(request.data)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Bad request.'}, 400    
    
    username = data.get('username')
    title = data.get('title')
    body = data.get('body')
    if not username:
        return {'error_msg': 'username invalid.'}, 400
    if not title:
        return {'error_msg': 'title invalid.'}, 400 
    if not body:
        return {'error_msg': 'body invalid.'}, 400
    context = {
        'username': username,
    }
    try:
        title = flask_app.app.assistant_manager.generate_title_from_content(title, body, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500
    return {'title': title}, 200
