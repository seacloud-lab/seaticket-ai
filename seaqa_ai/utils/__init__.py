# -*- coding: utf-8 -*-
import logging
import uuid


logger = logging.getLogger(__name__)


def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass


def uuid_str_to_36_chars(uuid_str):
    if isinstance(uuid_str, uuid.UUID):
        return str(uuid_str)
    if len(uuid_str) == 32:
        return str(uuid.UUID(uuid_str))
    else:
        return uuid_str


class LLMChatCompletionException(Exception):
    pass
