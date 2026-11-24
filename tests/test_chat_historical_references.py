import json
from copy import deepcopy
from unittest.mock import Mock

from seaqa_ai.services.chat_service.tools.featured_knowledge_base import GetFeaturedKnowledgeBase
from seaqa_ai.services.chat_service.utils.memory import ChatMemory
from seaqa_ai.utils import get_answer_and_sources
from seaqa_ai.utils.tools import GetRecords


PROJECT_UUID = '6f593763-a2f9-4b2f-95f9-791328b7b086'


def history_message(message_id, record_id, title, content):
    return {
        'id': message_id, 'role': 'assistant', 'content': content,
        'sources': [{'type': 'knowledge_base', 'connection_id': 'knowledge_base', '_id': record_id, 'title': title}],
    }


def test_historical_labels_are_local_to_each_answer_and_do_not_resolve_in_current_pool():
    old_answer = history_message(1, 42, 'Guide A', 'Use A[Reference 1]. Invalid[Reference 9]<reference_0, reference_2>.')
    later_answer = history_message(2, 43, 'Guide C', 'Use C[Reference 1].')
    original = deepcopy([old_answer, later_answer])
    memory = ChatMemory('Rules')
    memory += [old_answer, later_answer]
    _, first, second = list(memory)
    assert [old_answer, later_answer] == original
    for number, message, record_id, title in [(1, first, 42, 'Guide A'), (2, second, 43, 'Guide C')]:
        answer, mapping = message['content'].split('\n\nHistorical sources for this answer (context only):\n')
        label = f'H{number}-1'
        assert f'[Historical source {label}]' in answer
        assert '[Reference ' not in answer and '<reference_' not in answer
        assert json.loads(mapping) == [{
            'label': label, 'title': title,
            'identity': {'source_type': 'knowledge_base', 'connection_id': None, 'record_id': record_id},
        }]

    context = {'project_uuid': PROJECT_UUID}
    app = Mock()
    app.seadb_service.query_rows.return_value = {'results': [{'_pk': 7, 'title': 'Featured guide B'}]}
    [featured] = GetFeaturedKnowledgeBase().execute(context, app)
    assert featured['reference_label'] == '<reference_0>'
    pool = context['reference_pool']
    assert get_answer_and_sources(pool, 'Previously [Historical source H1-1].')[1] == []
    assert get_answer_and_sources(pool, 'New source<reference_0>.')[1][0]['_id'] == 7

    app.seadb_service.get_records_by_pks.side_effect = [
        {42: {'_pk': 42, 'title': 'Guide A'}}, {42: {'content': 'Instructions from A'}},
    ]
    identity = json.loads(first['content'].split('\n')[-1])[0]['identity']
    [record] = GetRecords().execute([identity], context, app)
    assert record['reference_label'] == '<reference_1>'
    assert get_answer_and_sources(pool, 'Earlier guide<reference_1>.')[1][0]['_id'] == 42


def test_featured_preload_queries_only_metadata_and_defers_body_to_get_records():
    context = {'project_uuid': PROJECT_UUID}
    app = Mock()
    app.seadb_service.query_rows.return_value = {'results': [{'_pk': 7, 'title': 'Featured guide'}]}
    [featured] = GetFeaturedKnowledgeBase().execute(context, app)
    assert '`content`' not in app.seadb_service.query_rows.call_args.args[1]
    assert 'content' not in featured
    assert context['loaded_records'].get('knowledge_base', None, 7) is None
    app.seadb_service.get_records_by_pks.assert_not_called()

    target = {key: featured[key] for key in ('source_type', 'connection_id', 'record_id')}
    app.seadb_service.get_records_by_pks.side_effect = [
        {7: {'_pk': 7, 'title': 'Featured guide'}}, {7: {'content': 'On-demand body'}},
    ]
    [record] = GetRecords().execute([target], context, app)
    assert record['reference_label'] == featured['reference_label']
    assert record['content'] == 'On-demand body'
    assert context['loaded_records'].get(**target)['content'] == 'On-demand body'
