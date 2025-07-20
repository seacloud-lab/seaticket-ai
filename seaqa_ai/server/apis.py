import logging
import jwt
import json

from flask import Flask, request
from seaqa_ai import config


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

    question = data.get('question')
    sources = data.get('sources')
    username = data.get('username')

    context = {
        'username': username,
    }

    if not question:
        return {'error_msg': 'question invalid.'}, 400
    if not sources:
        return {'error_msg': 'sources invalid.'}, 400
    if not username:
        return {'error_msg': 'username invalid.'}, 400

    try:
        answer = flask_app.app.text_processing_manager.generate_answer(question, sources, context)
    except Exception as e:
        logger.exception(e)
        return {'error_msg': 'Internet server error.'}, 500

    return {'answer': answer}, 200
