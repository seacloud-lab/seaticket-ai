"""One minimal, valid scenario per supported event type.

A scenario is a case plus the arguments to call every Handling tool of that
event with, so flow tests can run the whole workflow for any event without
writing event-specific data by hand.
"""
from dataclasses import dataclass

from seaqa_ai.agent.models.phase import AgentPhaseSpec

from tests.agent.support.builders import (
    github_issue_record,
    identity,
    make_case,
    ticket_record,
)


# event type -> (trigger source type, connection id, owned by a linked ticket)
EVENT_SOURCES = {
    'github_issue_added': ('github_issue', 1, False),
    'github_issue_comment_added': ('github_issue', 1, True),
    'discourse_topic_added': ('discourse_forum', 1, False),
    'discourse_topic_comment_added': ('discourse_forum', 1, True),
    'portal_issue_added': ('portal_issue', None, False),
    'portal_issue_comment_added': ('portal_issue', None, True),
    'discord_thread_added': ('discord', 1, False),
    'discord_thread_message_added': ('discord', 1, True),
    'email_thread_added': ('email', 1, False),
    'email_message_added': ('email', 1, True),
    'general_task_updated': ('general_task', 1, True),
    'jira_issue_updated': ('jira_issue', 1, True),
    'linear_issue_updated': ('linear', 1, True),
    'ticket_due_soon': ('ticket', None, False),
    'ticket_over_due': ('ticket', None, False),
}

TRIGGER_ID = 101
LINKED_TICKET_ID = 700
CANDIDATE_TICKET_ID = 900
EXTERNAL_CONNECTION_ID = 2
EXTERNAL_RECORD_ID = 50


@dataclass
class Scenario:
    event_type: str
    case: dict
    tool_arguments: dict
    # traced tool name -> (target_item_type, target_item_id)
    targets: dict
    linked: bool

    @property
    def trigger_identity(self):
        task = self.case['task']
        return identity(task['source_type'], task['connection_id'], task['record_id'])

    @property
    def owner_ticket_identity(self):
        if self.trigger_identity['source_type'] == 'ticket':
            return self.trigger_identity
        return identity('ticket', None, LINKED_TICKET_ID) if self.linked else None


def _trigger_record(source_type, record_id):
    if source_type == 'github_issue':
        return github_issue_record(record_id, available_labels=('bug', 'question'))
    if source_type == 'ticket':
        return ticket_record(record_id, title='Ticket', assignees=['alice'])
    return {
        '_pk': record_id,
        'title': f'A {source_type} record',
        'linked_ticket': None,
        'content': {'content': 'Body of the record.', 'latest_comment': None,
                    'history_comments': []},
    }


def scenario(event_type, *, settings=None):
    source_type, connection_id, linked = EVENT_SOURCES[event_type]
    record = _trigger_record(source_type, TRIGGER_ID)
    extra_records = [(
        'ticket', None,
        ticket_record(CANDIDATE_TICKET_ID, title='A candidate ticket', assignees=['alice']),
    )]
    connections = []
    linked_ticket = None
    if linked:
        linked_ticket = ticket_record(
            LINKED_TICKET_ID, title='Owner ticket', assignees=['alice'],
            linked_connection_records=[f'{EXTERNAL_CONNECTION_ID}_{EXTERNAL_RECORD_ID}'],
        )
        extra_records.append((
            'github_issue', EXTERNAL_CONNECTION_ID,
            github_issue_record(EXTERNAL_RECORD_ID, title='External issue'),
        ))
        connections.append(('github_issue', EXTERNAL_CONNECTION_ID))

    case = make_case(
        event_type,
        source_type=source_type,
        connection_id=connection_id,
        record_id=TRIGGER_ID,
        record=record,
        linked_ticket=linked_ticket,
        extra_records=extra_records,
        connections=connections,
        settings=settings,
        name=event_type,
    )

    trigger_source_id = str(TRIGGER_ID) if connection_id is None else f'{connection_id}_{TRIGGER_ID}'
    trigger_target = (source_type, trigger_source_id)
    if source_type == 'ticket':
        ticket_target = ('ticket', str(TRIGGER_ID))
    else:
        ticket_target = ('ticket', str(LINKED_TICKET_ID))

    tool_arguments = {
        'suggest_reply': {
            'reply': 'Thanks for the report. We are looking into it.',
            'reason': 'The reporter needs a visible acknowledgement.',
        },
        'suggest_create_ticket': {'reason': 'No existing ticket tracks this work.'},
        'suggest_link_existing_ticket': {
            'related_ticket': CANDIDATE_TICKET_ID,
            'reason': 'The candidate ticket tracks the same work.',
        },
        'suggest_notify_assignee': {
            'message': 'Please take a look at this update.',
            'reason': 'The update needs the owner attention.',
        },
        'suggest_close_ticket': {'reason': 'All linked work is done.'},
        'suggest_move_to_spam': {'reason': 'The thread looks like spam.'},
        'suggest_modify_type': {
            'current_issue_type': '',
            'suggested_type': 'Bug',
            'reason': 'The issue reports a crash.',
        },
        'suggest_assign_labels': {
            'suggested_labels': ['bug'],
            'reason': 'The issue reports a bug.',
        },
        'suggest_reply_external_targets': {
            'reply': 'The work is complete.',
            'reason': 'The external reporter should hear about it.',
            'reply_targets': [
                identity('github_issue', EXTERNAL_CONNECTION_ID, EXTERNAL_RECORD_ID),
            ],
        },
    }
    targets = {
        'suggest_reply': trigger_target,
        'suggest_create_ticket': trigger_target,
        'suggest_link_existing_ticket': trigger_target,
        'suggest_move_to_spam': trigger_target,
        'suggest_modify_type': trigger_target,
        'suggest_assign_labels': trigger_target,
        'suggest_notify_assignee': ticket_target,
        'suggest_close_ticket': ticket_target,
    }
    return Scenario(event_type, case, tool_arguments, targets, linked)


def handling_tools(event_type):
    return list(AgentPhaseSpec.get_skill_metadata(event_type, 'handling')['tools'])


def expected_trace(scenario_, tool_name):
    """(traced tool name, target type, target id) for one Handling tool call."""
    if tool_name == 'suggest_reply_external_targets':
        return ('suggest_reply', 'github_issue',
                f'{EXTERNAL_CONNECTION_ID}_{EXTERNAL_RECORD_ID}')
    target_type, target_id = scenario_.targets[tool_name]
    return tool_name, target_type, target_id
