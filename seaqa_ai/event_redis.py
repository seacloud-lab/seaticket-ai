# -*- coding: utf-8 -*-
import redis
import json
import logging
import time


logger = logging.getLogger(__name__)


class RedisClient(object):

    def __init__(self, host='', port=6379, password=None, socket_connect_timeout=30, socket_timeout=None):
        self._host = host
        self._port = port
        self._password = password

        """
        By default, each Redis instance created will in turn create its own connection pool.
        Every caller using redis client will has it's own pool with config caller passed.
        """
        self.connection = redis.StrictRedis(
            host=self._host, port=self._port, password=self._password,
            socket_timeout=socket_timeout, socket_connect_timeout=socket_connect_timeout,
            decode_responses=True
        )

    def publish(self, channel_name, message):
        self.connection.publish(channel_name, message)

    def get_subscriber(self, channel_name):
        while True:
            try:
                subscriber = self.connection.pubsub(ignore_subscribe_messages=True)
                subscriber.subscribe(channel_name)
            except redis.AuthenticationError as e:
                logger.critical('connect to redis auth error: %s', e)
                raise e
            except Exception as e:
                logger.error('redis pubsub failed. {} retry after 10s'.format(e))
                time.sleep(10)
            else:
                return subscriber
            
    def set(self, channel_name, key, value):
        try:
            hash_key = f"channel:{channel_name}:data"
            value = json.dumps(value)
            return self.connection.hset(hash_key, key, value)
        except Exception as e:
            logger.error(f'Failed to add value to channel {channel_name}: {e}')
            raise
    
    def mapping_set(self, channel_name, mapping):
        try:
            hash_key = f"channel:{channel_name}:data"
            mapping = {
                key: json.dumps(value)
                for key, value in mapping.items()
            }
            return self.connection.hset(hash_key, mapping=mapping)
        except Exception as e:
            logger.error(f'Failed to add value to channel {channel_name}: {e}')
            raise
    
    def delete(self, channel_name, key):
        try:
            hash_key = f"channel:{channel_name}:data"
            return self.connection.hdel(hash_key, key)
        except Exception as e:
            logger.warning(f'Failed to delete value from channel {channel_name}: {e}')
    
    def delete_channel(self, channel_name):
        try:
            hash_key = f"channel:{channel_name}:data"
            return self.connection.delete(hash_key)
        except Exception as e:
            logger.warning(f'Failed to delete channel data for {channel_name}: {e}')

    def get(self, channel_name, key, default=None):
        try:
            hash_key = f"channel:{channel_name}:data"
            return json.loads(self.connection.hget(hash_key, key))
        except:
            return default
        
    def get_all_data_from_channel(self, channel_name):
        try:
            hash_key = f"channel:{channel_name}:data"
            return {
                key: json.loads(value)
                for key, value in self.connection.hgetall(hash_key).items()
            }
        except:
            return {}
