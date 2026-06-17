from __future__ import annotations
import logging
from typing import Any, TYPE_CHECKING

from seaqa_ai import config
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES

if TYPE_CHECKING:
    from seaqa_ai.utils.tools import OpenAIToolExecutor


logger = logging.getLogger(__name__)
class BasicPseudoTool:
    MAX_SEARCH_COUNT: int = config.MAX_SEARCH_SOURCES_COUNT

    EMPTY_SEARCH_MESSAGE: str = ''
    SEARCH_LIMIT_MESSAGE: str = ''

    @staticmethod
    def _is_empty(output: Any) -> bool:
        if isinstance(output, str):
            return output in ('', '[]')
        if isinstance(output, list):
            return len(output) == 0
        return False

    @classmethod
    def enrich_tool_output(
        cls,
        tool_name: str,
        output: Any,
        tool_executor: OpenAIToolExecutor,
    ) -> Any:
        if not tool_name in SEARCH_TOOL_NAMES:
            return output

        if cls._is_empty(output):
            searched_records_num = len(tool_executor.cache.get('sources_results', []))
            if searched_records_num >= cls.MAX_SEARCH_COUNT:
                return cls.SEARCH_LIMIT_MESSAGE
            return cls.EMPTY_SEARCH_MESSAGE

        return output

    @classmethod
    def inject_max_step_pseudo_outputs(cls, *args, **kwargs) -> None:
        raise NotImplementedError
