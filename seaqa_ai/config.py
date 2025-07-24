import os
import sys
import logging

logger = logging.getLogger(__name__)

basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, basedir)


# Log
LOG_FILE = None
LOG_LEVEL = 'info'
ENABLE_SYS_LOG = False

APP_NAME = 'seaqa-ai'

# LLM
LLM_URL = ''
LLM_TYPE = 'openai-proxy'
LLM_KEY = ''
LLM_MODEL = 'gpt-4o-mini'

# Redis
REDIS_HOST = 'redis'
REDIS_PORT = 6379
REDIS_PASSWORD = ''

# SeaQA Index
SEAQA_INDEX_SERVER_URL = ''
JWT_PRIVATE_KEY = ''

CONF_DIR = '/opt/seaqa/conf/'

try:
    if os.path.exists('seaqa_ai_settings.py'):
        from seaqa_ai_settings import *
except:
    pass

try:
    if os.path.exists(CONF_DIR):
        sys.path.insert(0, CONF_DIR)
    from seaqa_ai_settings import *
except ImportError as e:
    pass

JWT_PRIVATE_KEY = os.environ.get('JWT_PRIVATE_KEY', '')

LLM_TYPE = os.getenv('SEAQA_AI_LLM_TYPE') or LLM_TYPE
LLM_URL = os.getenv('SEAQA_AI_LLM_URL') or LLM_URL
LLM_KEY = os.getenv('SEAQA_AI_LLM_KEY') or LLM_KEY
LLM_MODEL = os.getenv('SEAQA_AI_LLM_MODEL') or LLM_MODEL

LOG_LEVEL = os.getenv('seaqa_ai_LOG_LEVEL') or LOG_LEVEL

REDIS_HOST = os.getenv('REDIS_HOST') or REDIS_HOST
REDIS_PORT = os.getenv('REDIS_PORT') or REDIS_PORT
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD') or REDIS_PASSWORD
