import yaml

from seaqa_ai.agent.utils import get_skill_map


def test_prelude_skill_get_record_availability():
    skills = get_skill_map()
    expected_with_get_record = {
        'discourse_topic_comment_added',
        'discourse_topic_added',
        'email_message_added',
        'email_thread_added',
        'general_task_updated',
        'github_issue_added',
        'github_issue_comment_added',
    }

    for event, phases in skills.items():
        metadata = yaml.safe_load(phases['prelude'].split('====================', 1)[0])
        if event in {'ticket_due_soon', 'ticket_over_due'}:
            assert 'get_record' not in metadata['tools']
        elif event in expected_with_get_record:
            assert 'get_record' in metadata['tools']
