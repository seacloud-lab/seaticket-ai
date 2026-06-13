from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from seaqa_ai import config
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES

if TYPE_CHECKING:
    from seaqa_ai.utils.tools import OpenAIToolExecutor


class BasicPseudoTool:
    MAX_SEARCH_COUNT: int = config.MAX_SEARCH_SOURCES_COUNT

    EMPTY_SEARCH_MESSAGE: str = ''
    SEARCH_LIMIT_MESSAGE: str = ''

    @staticmethod
    def _is_search_tool(tool_name: str) -> bool:
        return tool_name in SEARCH_TOOL_NAMES

    @staticmethod
    def _get_search_count(tool_executor: OpenAIToolExecutor) -> int:
        return len(tool_executor.cache.get('sources_results', []))

    @staticmethod
    def _is_empty(output: Any) -> bool:
        if isinstance(output, str):
            return output in ('', '[]')
        if isinstance(output, list):
            return len(output) == 0
        return False

    @staticmethod
    def _serialize(output: Any) -> str:
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False)

    @classmethod
    def enrich_tool_output(
        cls,
        tool_name: str,
        output: Any,
        tool_executor: OpenAIToolExecutor,
    ) -> str:
        if not cls._is_search_tool(tool_name):
            return cls._serialize(output)

        if cls._is_empty(output):
            if cls._get_search_count(tool_executor) >= cls.MAX_SEARCH_COUNT:
                return cls.SEARCH_LIMIT_MESSAGE
            return cls.EMPTY_SEARCH_MESSAGE

        return cls._serialize(output)
