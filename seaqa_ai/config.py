import os
import sys
import logging
import json
from urllib.parse import quote_plus
import yaml

logger = logging.getLogger(__name__)
basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, basedir)

def _read_yaml(yaml_file_path=None, component_name=None):
    if yaml_file_path:
        # first read, get vars from global
        configs = {}
        try:
            with open(yaml_file_path, 'r', encoding='utf-8') as yaml_file:
                current_yaml_config = yaml.safe_load(yaml_file)
                configs = current_yaml_config.get('global', {})
                if component_name:
                    component_config = current_yaml_config.get(component_name, {})
                    configs.update(component_config)
                    if 'from_yaml' in component_config:
                        del configs['from_yaml']
                        configs.update(_read_yaml(component_config['from_yaml']))
        except Exception as e:
            logger.error(f'Failure to read YAML config file: {e}')
        return configs
    return {}

def _check_type(func):
    def wrapper(self, key, default=None, check_type=True):
        result = func(self, key, default)
        if check_type:
            need_type = type(default)
            if need_type in (int, float):
                try:
                    result = need_type(result)
                except:
                    raise ValueError(f'Type of {key} must be a number')
            elif need_type == bool:
                if isinstance(result, str):
                    result = result.lower() in ('true', '1')
                else:
                    result = bool(result)
            elif need_type in (str, list, dict):
                result = need_type(result)
        return result
    return wrapper

class _ConfigParser(object):
    def __init__(self, yaml_file_path, component_name):
        assert yaml_file_path and component_name, "yaml_file_path and component_name must be specified in initilizing ConfigParser"
        self.refresh_yaml_configs(yaml_file_path, component_name)
    
    def refresh_yaml_configs(self, yaml_file_path=None, component_name=None):
        self.yaml_file_path = yaml_file_path or self.yaml_file_path
        self.component_name = component_name or self.component_name
        self.yaml_configs = _read_yaml(self.yaml_file_path, self.component_name)
    
    @_check_type
    def get(self, key, default=None):
        if key in os.environ:
            value = os.getenv(key)
            try:
                value = json.loads(value)
            except:
                pass
            return value
        return self.yaml_configs.get(key, default)
    
def check_llm_validated(model):
    if not isinstance(model, dict) or model.get('disable', False):
        return False
    if model.get('type') in ('proxy', 'other', 'hosted_vllm'):
        required_fields = ('model', 'url')
    else:
        required_fields = ('model', 'key')
    return all(field in model for field in required_fields)

def get_llm_models_maps(models):
    if not models or not isinstance(models, list):
        return []
    model_id_models_map = {}
    tier_model_map = {}
    default_model = None

    for model in models:
        if not check_llm_validated(model):
            continue
        model['label'] = model.get('label', model['model'])
        model_id_models_map[model['model']] = model
        if model.get('tier') and model['tier'] not in tier_model_map:
            tier_model_map[model['tier']] = model
        if model.get('default', False) and not default_model:
            default_model = model
    if not model_id_models_map:
        raise ValueError('No valid LLM configurations')
    if not default_model:
        default_model = model_id_models_map[list(model_id_models_map.keys())[0]]

    return model_id_models_map, tier_model_map, default_model


CONF_DIR = os.getenv('CONF_PATH', '/opt/seaqa/conf/')
yaml_file_path = os.path.join(CONF_DIR, os.environ.get('SEAQA_CONFIG_NAME', 'seaqa_config.yaml'))
configs = _ConfigParser(yaml_file_path, 'seaqa-ai')

TIME_ZONE = configs.get('TIME_ZONE', 'UTC')

# Log
LOG_FILE = os.path.join(configs.get('LOG_PATH', '/tmp'), 'seaqa-ai.log')
LOG_LEVEL = configs.get('LOG_LEVEL', 'info')
ENABLE_SYS_LOG = configs.get('ENABLE_SYS_LOG', False)

APP_NAME = 'seaqa-ai'

# Database
MYSQL_HOST = configs.get('SEAQA_MYSQL_DB_HOST', 'db')
MYSQL_USER = configs.get('SEAQA_MYSQL_DB_USER', 'seaqa')
MYSQL_PASSWORD = configs.get('SEAQA_MYSQL_DB_PASSWORD', '')
MYSQL_PORT = configs.get('SEAQA_MYSQL_DB_PORT', 3306)
MYSQL_DB = configs.get('SEAQA_MYSQL_SEAQA_DB_NAME', 'sea_qa')
MYSQL_UNIX_SOCKET = os.getenv('DB_UNIX_SOCKET', '')

# LLM
LLM_MODEL_ID_MODELS_MAP, LLM_MODEL_TIER_MODELS_MAP, DEFAULT_LLM_MODEL = get_llm_models_maps(configs.get('LLM_MODELS', []))
EMBEDDING_MODEL = configs.get('EMBEDDING_MODEL', {})
if not check_llm_validated(EMBEDDING_MODEL):
    raise ValueError('EMBEDDING_MODEL is not set or invalid')

## AI_UTILS
AI_UTILS_TIER = configs.get('AI_UTILS_TIER', {})

## CONTEXT
DISABLE_CONTEXT = configs.get('DISABLE_CONTEXT', False)
CONTEXT_WINDOW_LIMIT = configs.get('CONTEXT_WINDOW_LIMIT', 10)
CONTEXT_HISTORY_VALID_TIME = configs.get('CONTEXT_HISTORY_VALID_TIME', 168)

## Max steps
MAX_STEPS = configs.get('MAX_STEPS', 5)
assert MAX_STEPS > 0, "MAX_STEPS must larger than 0"

## Thought process
THOUGHT_PROCESS_CONFIGS = configs.get('THOUGHT_PROCESS', {})

### Task
THOUGHT_PROCESS_TASKS_CONFIGS = THOUGHT_PROCESS_CONFIGS.get('tasks', {})
THOUGHT_PROCESS_ENABLE_RECORD_TASKS = THOUGHT_PROCESS_TASKS_CONFIGS.get('enabled', True)
THOUGHT_PROCESS_RECORD_SYSTEM_PROMPT = THOUGHT_PROCESS_TASKS_CONFIGS.get('system_prompt', False)
THOUGHT_PROCESS_RECORD_USER_INPUT = THOUGHT_PROCESS_TASKS_CONFIGS.get('user_input', True)
THOUGHT_PROCESS_RECORD_USER_INPUT_RAW = THOUGHT_PROCESS_TASKS_CONFIGS.get('user_input_raw', False)

### context
THOUGHT_PROCESS_RECORD_CONTEXT = THOUGHT_PROCESS_CONFIGS.get('context', False)

### Tools
ENABLE_TEST_TOOL = configs.get('ENABLE_TEST_TOOL', False)
THOUGHT_PROCESS_RECORD_TOOL_DETAILS = THOUGHT_PROCESS_CONFIGS.get('tool_details', False)
SEARCH_TOOL_CONTENT_MAX_SIZE = configs.get('SEARCH_TOOL_CONTENT_MAX_SIZE', 20000)
SEARCH_TOOL_ISSUES_MAX_COMMENTS = configs.get('SEARCH_TOOL_ISSUES_MAX_COMMENTS', 50)

# Redis
REDIS_HOST = configs.get('REDIS_HOST', 'redis')
REDIS_PORT = configs.get('REDIS_PORT', 6379)
REDIS_PASSWORD = configs.get('REDIS_PASSWORD', '')

# SeaQA Indexer
SEAQA_INDEXER_INNER_SERVER_URL = configs.get('SEAQA_INDEXER_INNER_SERVER_URL', 'http://seaqa-indexer:8888')
JWT_PRIVATE_KEY = configs.get('JWT_PRIVATE_KEY', '')

# SeaDB
SEADB_SERVER_URL = configs.get('SEADB_SERVER_URL', 'http://seadb:8888')
SEADB_SERVER_ACCESS_TOKEN = configs.get('SEADB_SERVER_ACCESS_TOKEN', '')

# S3
S3_HOST = configs.get('S3_HOST', '')
S3_WEB_CRAWL_BUCKET = configs.get('S3_WEB_CRAWL_BUCKET', '')
S3_KEY_ID = configs.get('S3_KEY_ID', '')
S3_SECRET_KEY = configs.get('S3_SECRET_KEY', '')

SQLALCHEMY_DATABASE_URI = "mysql+pymysql://%s:%s@%s:%s/%s?charset=utf8" % \
                          (MYSQL_USER, quote_plus(MYSQL_PASSWORD), MYSQL_HOST, MYSQL_PORT, MYSQL_DB)

if MYSQL_UNIX_SOCKET:
    SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI + '&unix_socket=' + MYSQL_UNIX_SOCKET

# Agent
AGENT_MAX_TICKETS_PER_RUN = configs.get('AGENT_MAX_TICKETS_PER_RUN', 100)
AGENT_MAX_GITHUB_ISSUES_PER_RUN = configs.get('AGENT_MAX_GITHUB_ISSUES_PER_RUN', 100)
AGENT_MAX_STEPS = configs.get('AGENT_MAX_STEPS', 20)
