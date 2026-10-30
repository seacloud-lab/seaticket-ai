"""Capture a public GitHub issue as an agent golden-case input.

Example:
    python tests/agent/tools/capture_github_issue_case.py \
        --repo haiwen/seafile --issue 3050 \
        --out tests/agent/golden/cases/github_issue_added/seafile_issue_3050
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml


API = 'https://api.github.com'


class LiteralDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper, data):
    style = '|' if '\n' in data else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)


LiteralDumper.add_representer(str, _represent_str)


def _request(url):
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'seaqa-ai-tests',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
        return payload, response.headers


def _get_json(path):
    payload, _headers = _request(API + path)
    return payload


def _get_pages(path):
    url = API + path
    items = []
    while url:
        payload, headers = _request(url)
        if isinstance(payload, list):
            items.extend(payload)
        else:
            return payload
        url = None
        for part in headers.get('Link', '').split(','):
            if 'rel="next"' in part:
                url = part[part.find('<') + 1:part.find('>')]
    return items


def _actor(event):
    actor = event.get('actor') or {}
    return actor.get('login') or ''


def _reference_events(timeline):
    events = []
    for event in timeline:
        kind = event.get('event') or ''
        interesting = kind in {'labeled', 'unlabeled'} or 'type' in kind or 'label' in kind
        if not interesting:
            continue
        label = (event.get('label') or {}).get('name')
        renamed = event.get('rename') or {}
        detail = label or renamed.get('to') or event.get('name') or ''
        if not detail and isinstance(event.get('issue_type'), dict):
            detail = event['issue_type'].get('name') or ''
        events.append({
            'at': event.get('created_at') or '',
            'actor': _actor(event),
            'event': kind,
            'detail': detail,
        })
    return events


def build_input(repo, number, connection_id):
    owner = repo.split('/', 1)[0]
    issue = _get_json(f'/repos/{repo}/issues/{number}')
    labels = _get_pages(f'/repos/{repo}/labels?per_page=100')
    timeline = _get_pages(f'/repos/{repo}/issues/{number}/timeline?per_page=100')
    try:
        issue_types = _get_json(f'/orgs/{owner}/issue-types')
    except urllib.error.HTTPError as error:
        print(
            f'warning: could not read org issue types ({error.code}); '
            'available_issue_types left empty',
            file=sys.stderr,
        )
        issue_types = []

    label_names = sorted({
        str(item.get('name') or '').strip()
        for item in labels
        if str(item.get('name') or '').strip()
    })
    type_names = []
    for item in issue_types if isinstance(issue_types, list) else []:
        name = str(item.get('name') or '').strip()
        if name:
            type_names.append(name)

    author = (issue.get('user') or {}).get('login') or ''
    body = issue.get('body') or ''
    title = issue.get('title') or ''
    created_at = issue.get('created_at')
    record_id = int(number)
    return {
        'source': issue.get('html_url'),
        'project': {
            'settings': {
                'lang': 'en',
                'agent': {'model': None},
            },
            'issue_connections': [{'id': connection_id, 'type': 'github_issue'}],
            'document_connections': [],
            'task_connections': [],
        },
        'task': {
            'source_type': 'github_issue',
            'connection_id': connection_id,
            'record_id': record_id,
            'modified_time': created_at,
            'event': {
                'type': 'github_issue_added',
                'old_value': {},
                'new_value': {
                    'title': title,
                    'content': body,
                },
            },
        },
        'records': [{
            'identity': {
                'source_type': 'github_issue',
                'connection_id': connection_id,
                'record_id': record_id,
            },
            'record': {
                '_pk': record_id,
                'issue_number': int(issue.get('number') or number),
                'title': title,
                'state': 'open',
                'labels': [],
                'issue_type': '',
                'author': author,
                'url': issue.get('html_url'),
                'created_time': created_at,
                'content': {
                    'content': body,
                    'latest_comment': None,
                    'history_comments': [],
                },
                'available_labels': label_names,
            },
        }],
        'available_issue_types': type_names,
        'search_results': {
            'issues_search': [],
            'documents_search': [],
        },
        '_timeline_notes': _reference_events(timeline),
        '_current_type': (issue.get('type') or {}).get('name') if isinstance(issue.get('type'), dict) else '',
        '_current_labels': [
            item.get('name') for item in issue.get('labels') or [] if isinstance(item, dict)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='owner/name, for example haiwen/seafile')
    parser.add_argument('--issue', required=True, type=int)
    parser.add_argument('--out', required=True, help='case directory to create')
    parser.add_argument('--connection-id', type=int, default=1)
    args = parser.parse_args()

    captured = build_input(args.repo, args.issue, args.connection_id)
    notes = captured.pop('_timeline_notes')
    current_type = captured.pop('_current_type')
    current_labels = captured.pop('_current_labels')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'input.yaml').open('w', encoding='utf-8') as handle:
        yaml.dump(
            captured,
            handle,
            Dumper=LiteralDumper,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )
    # What actually happened later is a historical fact, kept apart from the
    # rules in expected.yaml that say what counts as correct.
    reference = {
        'source': captured['source'],
        'final_type': current_type or '',
        'final_labels': current_labels,
        'timeline': notes,
    }
    with (out / 'reference.yaml').open('w', encoding='utf-8') as handle:
        yaml.dump(reference, handle, allow_unicode=True, sort_keys=False, width=100)
    print(f'wrote {out / "input.yaml"}')
    print(f'wrote {out / "reference.yaml"}')
    print(f'current type: {current_type or "<empty>"}')
    print(f'current labels: {", ".join(current_labels) or "<none>"}')
    print('write expected.yaml by hand; see the existing cases for the format')


if __name__ == '__main__':
    main()
