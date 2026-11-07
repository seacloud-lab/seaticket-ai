"""Check an agent run against the rules in an ``expected.yaml`` file.

A rule names what to check and the matchers to check it with; the value must
satisfy every matcher listed:

    suggested_labels: {includes_any: [type-possiblyabug], length_between: [1, 3]}

Matcher arguments that start with ``$`` are looked up in the case variables,
for example ``$available_labels`` or ``$trigger_record.title``.
"""
import re


class _Variables:
    def __init__(self, values):
        self.values = values or {}

    def resolve(self, arg):
        if isinstance(arg, list):
            return [self.resolve(item) for item in arg]
        if not (isinstance(arg, str) and arg.startswith('$')):
            return arg
        current = self.values
        for part in arg[1:].split('.'):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise KeyError(f'unknown variable {arg}')
        return current


def _fold(value):
    return str(value).casefold()


def _as_list(value):
    return value if isinstance(value, list) else []


def _equals(value, arg):
    return value == arg


def _one_of(value, arg):
    return value in arg


def _includes_all(value, arg):
    have = {_fold(item) for item in _as_list(value)}
    return all(_fold(item) in have for item in arg)


def _includes_any(value, arg):
    have = {_fold(item) for item in _as_list(value)}
    return any(_fold(item) in have for item in arg)


def _subset_of(value, arg):
    allowed = {_fold(item) for item in arg}
    return all(_fold(item) in allowed for item in _as_list(value))


def _length_between(value, arg):
    if not isinstance(value, (list, str)):
        return False
    return arg[0] <= len(value) <= arg[1]


def _not_empty(value, arg):
    return bool(str(value or '').strip()) == arg


def _contains_any(value, arg):
    text = _fold(value or '')
    return any(_fold(item) in text for item in arg)


def _not_contains(value, arg):
    text = _fold(value or '')
    return not any(_fold(item) in text for item in arg)


def _matches_regex(value, arg):
    return re.search(arg, str(value or '')) is not None


# name -> (function, kind of argument)
MATCHERS = {
    'equals': (_equals, 'any'),
    'one_of': (_one_of, 'list'),
    'includes_all': (_includes_all, 'list'),
    'includes_any': (_includes_any, 'list'),
    'subset_of': (_subset_of, 'list_or_variable'),
    'length_between': (_length_between, 'pair'),
    'not_empty': (_not_empty, 'bool'),
    'contains_any': (_contains_any, 'list'),
    'not_contains': (_not_contains, 'list'),
    'matches_regex': (_matches_regex, 'str'),
}


def validate_matcher(matcher, where):
    """Reject a rule with no matcher, an unknown matcher, or a wrong kind of argument.

    A rule may combine several matchers; the value must satisfy all of them.
    """
    if not isinstance(matcher, dict) or not matcher:
        raise ValueError(f'{where}: a rule needs at least one matcher, got {matcher!r}')
    for name, arg in matcher.items():
        if name not in MATCHERS:
            raise ValueError(f'{where}: unknown matcher {name!r}; known: {sorted(MATCHERS)}')
        kind = MATCHERS[name][1]
        is_variable = isinstance(arg, str) and arg.startswith('$')
        ok = {
            'any': True,
            'list': isinstance(arg, list),
            'list_or_variable': isinstance(arg, list) or is_variable,
            'pair': isinstance(arg, list) and len(arg) == 2
                    and all(isinstance(x, int) for x in arg),
            'bool': isinstance(arg, bool),
            'str': isinstance(arg, str),
        }[kind]
        if not ok:
            raise ValueError(f'{where}: matcher {name!r} needs a {kind} argument, got {arg!r}')


def validate_matchers(expected):
    """Check that every matcher in an expected.yaml dict is known and well formed."""
    expected = expected or {}
    for field, matcher in (expected.get('analysis') or {}).items():
        validate_matcher(matcher, f'analysis.{field}')
    suggestions = expected.get('suggestions') or {}
    for index, call in enumerate(suggestions.get('calls') or []):
        where = f'suggestions.calls[{index}]({call.get("tool")})'
        for field, matcher in (call.get('payload') or {}).items():
            validate_matcher(matcher, f'{where}.payload.{field}')
        for field in ('content', 'reason'):
            if field in call:
                validate_matcher(call[field], f'{where}.{field}')
        for field, matcher in (call.get('target') or {}).items():
            validate_matcher(matcher, f'{where}.target.{field}')
    search = expected.get('search') or {}
    if 'first_tool' in search:
        validate_matcher(search['first_tool'], 'search.first_tool')


def _check(matcher, value, variables, where, failures):
    for name, arg in matcher.items():
        function = MATCHERS[name][0]
        try:
            arg = variables.resolve(arg)
            passed = function(value, arg)
        except KeyError as error:
            failures.append(f'{where}: {error.args[0]}')
            continue
        if not passed:
            failures.append(f'{where}: {name} {arg!r} failed, value was {value!r}')


def evaluate(result, expected, variables=None):
    """Return a list of failure messages. An empty list means the run passed."""
    failures = []
    variables = _Variables(variables)
    expected = expected or {}

    analysis = result.phases.get('analysis') or {}
    for field, matcher in (expected.get('analysis') or {}).items():
        _check(matcher, analysis.get(field), variables, f'analysis.{field}', failures)

    suggestion_rules = expected.get('suggestions') or {}
    names = result.tool_names

    group = suggestion_rules.get('exactly_one_of')
    if group is not None:
        called = [name for name in names if name in group]
        if len(called) != 1:
            failures.append(
                f'suggestions: expected exactly one of {group}, called {called or "none"}'
            )
    for name in suggestion_rules.get('forbidden') or []:
        if name in names:
            failures.append(f'suggestions: {name} must not be called')

    for call in suggestion_rules.get('calls') or []:
        tool = call['tool']
        found = next((item for item in result.suggestions if item['tool_name'] == tool), None)
        if found is None:
            failures.append(f'suggestions: {tool} was not called')
            continue
        where = f'suggestions.{tool}'
        for field, matcher in (call.get('payload') or {}).items():
            payload = found.get('suggestion_payload') or {}
            _check(matcher, payload.get(field), variables, f'{where}.payload.{field}', failures)
        if 'content' in call:
            _check(call['content'], found.get('suggestion_content'), variables,
                   f'{where}.content', failures)
        if 'reason' in call:
            _check(call['reason'], found.get('suggestion_reason'), variables,
                   f'{where}.reason', failures)
        target_values = {
            'type': found.get('target_item_type'),
            'id': found.get('target_item_id'),
        }
        for field, matcher in (call.get('target') or {}).items():
            _check(matcher, target_values.get(field), variables,
                   f'{where}.target.{field}', failures)

    search_rules = expected.get('search') or {}
    if 'first_tool' in search_rules:
        first = result.search_calls[0]['tool'] if result.search_calls else None
        _check(search_rules['first_tool'], first, variables, 'search.first_tool', failures)
    if 'max_calls' in search_rules and len(result.search_calls) > search_rules['max_calls']:
        failures.append(
            f'search: {len(result.search_calls)} calls, at most {search_rules["max_calls"]} allowed'
        )
    return failures
