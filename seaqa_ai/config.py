import os
import sys
import logging
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, basedir)

TIME_ZONE = 'UTC'

# Log
LOG_FILE = None
LOG_LEVEL = 'info'
ENABLE_SYS_LOG = False

APP_NAME = 'seaqa-ai'

# Database
MYSQL_HOST = 'db'
MYSQL_USER = 'seaqa'
MYSQL_PASSWORD = ''
MYSQL_PORT = 3306
MYSQL_DB = 'sea_qa'
MYSQL_UNIX_SOCKET = os.getenv('DB_UNIX_SOCKET', '')

# LLM
LLM_URL = ''
LLM_TYPE = 'proxy'
LLM_KEY = ''
LLM_MODEL = 'gpt-4o-mini'

## CONTEXT
DISABLE_CONTEXT = False
CONTEXT_WINDOW_LIMIT = 10
CONTEXT_HISTORY_VALID_TIME = 168 # hour

# Redis
REDIS_HOST = 'redis'
REDIS_PORT = 6379
REDIS_PASSWORD = ''

# SeaQA Indexer
SEAQA_INDEXER_SERVER_URL = ''
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

TIME_ZONE = os.getenv('TIME_ZONE') or TIME_ZONE

JWT_PRIVATE_KEY = os.environ.get('JWT_PRIVATE_KEY', '')

LLM_TYPE = os.getenv('SEAQA_AI_LLM_TYPE') or LLM_TYPE
LLM_URL = os.getenv('SEAQA_AI_LLM_URL') or LLM_URL
LLM_KEY = os.getenv('SEAQA_AI_LLM_KEY') or LLM_KEY
LLM_MODEL = os.getenv('SEAQA_AI_LLM_MODEL') or LLM_MODEL

LOG_LEVEL = os.getenv('SEAQA_AI_LOG_LEVEL') or LOG_LEVEL

REDIS_HOST = os.getenv('REDIS_HOST') or REDIS_HOST
REDIS_PORT = os.getenv('REDIS_PORT') or REDIS_PORT
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD') or REDIS_PASSWORD

# database
MYSQL_HOST = os.getenv('SEAQA_MYSQL_DB_HOST') or MYSQL_HOST
MYSQL_USER = os.getenv('SEAQA_MYSQL_DB_USER') or MYSQL_USER
MYSQL_PASSWORD = os.getenv('SEAQA_MYSQL_DB_PASSWORD') or MYSQL_PASSWORD
MYSQL_PORT = int(os.getenv('SEAQA_MYSQL_DB_PORT')) if os.getenv('SEAQA_MYSQL_DB_PORT') else MYSQL_PORT
MYSQL_DB = os.getenv('SEAQA_MYSQL_SEAQA_DB_NAME') or MYSQL_DB

SQLALCHEMY_DATABASE_URI = "mysql+pymysql://%s:%s@%s:%s/%s?charset=utf8" % \
                          (MYSQL_USER, quote_plus(MYSQL_PASSWORD), MYSQL_HOST, MYSQL_PORT, MYSQL_DB)

if MYSQL_UNIX_SOCKET:
    SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI + '&unix_socket=' + MYSQL_UNIX_SOCKET
