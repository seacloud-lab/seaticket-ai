# -*- coding: utf-8 -*-
import logging

logger = logging.getLogger(__name__)


class DataLogging(object):

    def __init__(self, redis_client):
        self.redis_client = redis_client

    def log_data(self, channel_name, message):
        try:
            self.redis_client.publish(channel_name, message)
        except Exception as e:
            logger.warning('publish message: %s, to channel: %s, error: %s .', message, channel_name, e)
