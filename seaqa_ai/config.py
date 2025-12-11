import os
import sys
import logging
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
            if (need_type := type(default)) in (int, float):
                try:
                    result = need_type(result)
                except:
                    raise ValueError(f'Type of {key} must be a number')
            elif need_type == bool:
                if isinstance(result, str):
                    result = result.lower() in ('true', '1')
                else:
                    result = bool(result)
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
        return os.getenv(key) if key in os.environ else self.yaml_configs.get(key, default)


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
LLM_MODELS = configs.get('LLM_MODELS', [])

def check_model_validation(model_config):
    if not isinstance(model_config, dict):
        return False
    if model_config.get('type') == 'proxy':
        required_fields = ('model', 'url')
    else:
        required_fields = ('model', 'key')
    if not all(field in model_config for field in required_fields):
        return False
    
    return True

def get_useful_llm_configs_from_model_config(model_config):
    return {
        'model': model_config.get('model'),
        'type': model_config.get('type', 'openai'),
        'url': model_config.get('url'),
        'key': model_config.get('key')
    }
    

def get_default_model_config():
    if not LLM_MODELS:
        raise ValueError("LLM_MODELS is empty. Please configure at least one model.")

    first_model = None
    for model_config in LLM_MODELS:
        if not check_model_validation(model_config):
            continue

        if model_config.get('default') == True:
            return get_useful_llm_configs_from_model_config(model_config)
        elif not first_model:
            first_model = get_useful_llm_configs_from_model_config(model_config)

    if first_model:
        return first_model

    raise ValueError("No valid model configuration in LLM_MODELS.")

def get_light_weight_model_config():
    if not LLM_MODELS:
        raise ValueError("LLM_MODELS is empty. Please configure at least one model.")

    for model_config in LLM_MODELS:
        if not check_model_validation(model_config):
            continue

        if model_config.get('lightweight_task_llm_model') == True:
            return get_useful_llm_configs_from_model_config(model_config)

    return get_default_model_config()

def get_model_config_by_id(model_id):
    for model_config in LLM_MODELS:
        if not isinstance(model_config, dict):
            continue
        if model_config.get('model') != model_id:
            continue

        if not check_model_validation(model_config):
            raise ValueError(f"Model '{model_id}' missing required config fields")

        return get_useful_llm_configs_from_model_config(model_config)
    raise ValueError(f"Model '{model_id}' not found. ")

EMBEDDING_MODEL = configs.get('EMBEDDING_MODEL', {})
if check_model_validation(EMBEDDING_MODEL):
    EMBEDDING_MODEL = get_useful_llm_configs_from_model_config(EMBEDDING_MODEL)
else:
    raise ValueError('EMBEDDING_MODEL is not set or invalid')

## CONTEXT
DISABLE_CONTEXT = configs.get('DISABLE_CONTEXT', False)
CONTEXT_WINDOW_LIMIT = configs.get('CONTEXT_WINDOW_LIMIT', 10)
CONTEXT_HISTORY_VALID_TIME = configs.get('CONTEXT_HISTORY_VALID_TIME', 168)

# Redis
REDIS_HOST = configs.get('REDIS_HOST', 'redis')
REDIS_PORT = configs.get('REDIS_PORT', 6379)
REDIS_PASSWORD = configs.get('REDIS_PASSWORD', '')

# SeaQA Indexer
SEAQA_INDEXER_INNER_SERVER_URL = configs.get('SEAQA_INDEXER_INNER_SERVER_URL', '')
JWT_PRIVATE_KEY = configs.get('JWT_PRIVATE_KEY', '')

SQLALCHEMY_DATABASE_URI = "mysql+pymysql://%s:%s@%s:%s/%s?charset=utf8" % \
                          (MYSQL_USER, quote_plus(MYSQL_PASSWORD), MYSQL_HOST, MYSQL_PORT, MYSQL_DB)

if MYSQL_UNIX_SOCKET:
    SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI + '&unix_socket=' + MYSQL_UNIX_SOCKET
