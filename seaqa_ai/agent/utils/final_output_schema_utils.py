from copy import deepcopy


class FinalOutputSchemaUtils:
    """Build, validate, and adapt phase final-output JSON Schemas.

    This utility covers three stages of the phase-output lifecycle:
    1. Build a phase schema by combining framework fields with skill-defined output.
    2. Validate and normalize an LLM's final output against that schema.
    3. Adapt the schema to the strict JSON Schema format accepted by the LLM API.
    """

    @classmethod
    def build(cls, base_schema, additional_output, event, phase):
        """Return the effective output schema for one event phase.

        ``base_schema`` contains framework-owned fields such as
        ``analysis_report``. ``additional_output`` comes from the event skill.
        A skill may add a new field or refine an existing field's nested
        constraints, but it cannot change protected attributes such as its type
        or required status. Inputs are deep-copied so the shared base schema is
        never mutated.

        Example based on ``skills/github_issue_added/analysis.md``::

            # AnalysisPhaseSpec already defines recommended_actions as an array
            # of strings. The skill metadata narrows its items and adds two
            # event-specific fields.
            base_schema = {
                'type': 'object',
                'properties': {
                    'analysis_report': {'type': 'string'},
                    'recommended_actions': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'skip_handling': {'type': 'boolean'},
                },
                'required': [
                    'analysis_report',
                    'recommended_actions',
                    'skip_handling',
                ],
            }
            additional_output = {
                'recommended_actions': {
                    'items': {
                        'enum': [
                            'suggest_modify_type',
                            'suggest_assign_labels',
                            'suggest_reply',
                            'suggest_create_ticket',
                            'no_op',
                        ],
                    },
                },
                'original_issue_type': {
                    'type': 'string',
                    'description': 'The exact issue type supplied by the source record.',
                },
                'classified_issue_type': {
                    'type': 'string',
                    'enum': ['Bug', 'Feature', 'Question'],
                    'description': 'The issue type classified by Analysis.',
                },
            }

            # The existing item type is retained and receives the skill enum:
            result['properties']['recommended_actions']['items'] == {
                'type': 'string',
                'enum': [
                    'suggest_modify_type',
                    'suggest_assign_labels',
                    'suggest_reply',
                    'suggest_create_ticket',
                    'no_op',
                ],
            }

            # New skill fields default to required and are appended:
            result['required'] == [
                'analysis_report',
                'recommended_actions',
                'skip_handling',
                'original_issue_type',
                'classified_issue_type',
            ]
        """
        cls._validate_additional_output(additional_output, event, phase, base_schema)
        schema = deepcopy(base_schema)
        properties = schema.setdefault('properties', {})
        for name, property_schema in additional_output.items():
            is_base_field_override = name in properties
            output_schema = {
                key: value
                for key, value in property_schema.items()
                if key != 'required'
            }
            if is_base_field_override:
                properties[name] = cls._deep_merge_schema(properties[name], output_schema)
            else:
                properties[name] = output_schema
                if property_schema.get('required', True):
                    schema.setdefault('required', []).append(name)
        return schema if properties else {}

    @classmethod
    def _validate_additional_output(cls, additional_output, event, phase, base_schema):
        """Validate skill-defined schema additions before merging them.

        New fields must provide a type and description. Overrides of framework
        fields are checked against the corresponding base field. Enum values and
        optional defaults are also checked against the resulting field schema.
        """
        if additional_output is None:
            return
        if not isinstance(additional_output, dict):
            raise ValueError(f'Skill {event}/{phase} additional_output must be an object')
        if not additional_output:
            raise ValueError(f'Skill {event}/{phase} additional_output must not be empty')

        base_properties = base_schema.get('properties', {})
        for name, property_schema in additional_output.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f'Skill {event}/{phase} additional_output property names must be strings')
            if not isinstance(property_schema, dict):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} must be an object'
                )
            is_base_field_override = name in base_properties
            if is_base_field_override:
                cls._validate_base_field_override(base_properties[name], property_schema, event, phase, name)
            if not is_base_field_override and not isinstance(property_schema.get('type'), str):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} requires a type'
                )
            if not is_base_field_override and (
                not isinstance(property_schema.get('description'), str)
                or not property_schema['description'].strip()
            ):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} requires a description'
                )
            required = property_schema.get('required', True)
            if not isinstance(required, bool):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} required must be boolean'
                )
            if is_base_field_override and 'required' in property_schema:
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} cannot change required'
                )
            if required and 'default' in property_schema:
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} default requires required: false'
                )
            effective_schema = (
                cls._deep_merge_schema(base_properties[name], property_schema)
                if is_base_field_override else property_schema
            )
            cls._validate_property_enum_values(effective_schema, event, phase, name)
            if 'default' in property_schema and not cls._value_matches_schema(
                property_schema['default'], effective_schema,
            ):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} default does not match its schema'
                )

    @classmethod
    def _deep_merge_schema(cls, base_schema, overrides):
        """Deep-copy a schema and recursively apply skill-provided overrides.

        Nested dictionaries such as ``items`` are merged instead of replaced.
        ``required`` is intentionally ignored because skill output is not
        allowed to change the framework's required-field contract.
        """
        schema = deepcopy(base_schema)
        for key, value in overrides.items():
            if key == 'required':
                continue
            if isinstance(schema.get(key), dict) and isinstance(value, dict):
                schema[key] = cls._deep_merge_schema(schema[key], value)
            else:
                schema[key] = deepcopy(value)
        return schema

    @classmethod
    def _validate_base_field_override(cls, base_schema, overrides, event, phase, name):
        """Ensure a skill refinement preserves the base field's nested types."""
        if not isinstance(base_schema, dict) or not isinstance(overrides, dict):
            return
        if 'type' in overrides and overrides['type'] != base_schema.get('type'):
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} cannot change type'
            )
        for key, value in overrides.items():
            if isinstance(base_schema.get(key), dict) and isinstance(value, dict):
                cls._validate_base_field_override(base_schema[key], value, event, phase, name)

    @classmethod
    def _validate_property_enum_values(cls, property_schema, event, phase, name):
        """Recursively ensure every enum value matches its containing schema."""
        if not isinstance(property_schema, dict):
            return
        enum = property_schema.get('enum')
        if enum is not None:
            if not isinstance(enum, list):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} enum must be an array'
                )
            schema_without_enum = {key: value for key, value in property_schema.items() if key != 'enum'}
            if any(not cls._value_matches_schema(value, schema_without_enum) for value in enum):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} enum does not match its schema'
                )
        if isinstance(property_schema.get('items'), dict):
            cls._validate_property_enum_values(property_schema['items'], event, phase, name)
        for nested_property_schema in (property_schema.get('properties') or {}).values():
            cls._validate_property_enum_values(nested_property_schema, event, phase, name)

    @staticmethod
    def _value_matches_schema(value, schema):
        if 'enum' in schema and value not in schema['enum']:
            return False
        type_checks = {
            'object': lambda item: isinstance(item, dict),
            'array': lambda item: isinstance(item, list),
            'string': lambda item: isinstance(item, str),
            'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
            'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            'boolean': lambda item: isinstance(item, bool),
            'null': lambda item: item is None,
        }
        try:
            return type_checks[schema['type']](value)
        except KeyError:
            return False

    @classmethod
    def validate_and_normalize_output(cls, value, schema, error_type, path='$'):
        """Validate an LLM final output and return its normalized representation.

        Validation enforces ``const``, ``enum``, type, and required-field
        constraints recursively. During object normalization, undeclared fields
        are omitted, missing optional fields receive their schema default (or
        ``None``), and nested objects and arrays are normalized in the same way.
        ``path`` is carried through recursion to produce precise error messages.

        Example using the effective ``github_issue_added`` Analysis schema::

            value = {
                'analysis_report': 'The issue describes a reproducible failure.',
                'recommended_actions': [
                    'suggest_modify_type',
                    'suggest_create_ticket',
                ],
                'skip_handling': False,
                'original_issue_type': '',
                'classified_issue_type': 'Bug',
                'model_debug_note': 'must not reach downstream phases',
            }

            # All declared values pass their framework and skill constraints.
            # The undeclared model_debug_note is omitted from normalized output.
            normalized = {
                'analysis_report': 'The issue describes a reproducible failure.',
                'recommended_actions': [
                    'suggest_modify_type',
                    'suggest_create_ticket',
                ],
                'skip_handling': False,
                'original_issue_type': '',
                'classified_issue_type': 'Bug',
            }

            # A value such as classified_issue_type='Incident' would fail at
            # $.classified_issue_type because the skill permits only Bug,
            # Feature, or Question.
        """
        if not isinstance(schema, dict):
            raise error_type(f'{path}: schema must be an object')
        if 'const' in schema and value != schema['const']:
            raise error_type(f'{path}: must equal {schema["const"]!r}')
        if 'enum' in schema and value not in schema['enum']:
            raise error_type(f'{path}: must be one of {schema["enum"]!r}')

        allowed_types = schema.get('type')
        if allowed_types is not None:
            allowed_types = allowed_types if isinstance(allowed_types, list) else [allowed_types]
            if not any(cls._matches_type(value, expected_type, error_type) for expected_type in allowed_types):
                raise error_type(f'{path}: expected type {allowed_types!r}, got {type(value).__name__}')

        if isinstance(value, dict):
            properties = schema.get('properties') or {}
            required = schema.get('required') or []
            missing = [name for name in required if name not in value]
            if missing:
                raise error_type(f'{path}: missing required fields: {", ".join(missing)}')
            normalized = {}
            for name, item_schema in properties.items():
                if name not in value:
                    if name not in required:
                        normalized[name] = deepcopy(item_schema.get('default'))
                    continue
                item = value[name]
                if item is None and name not in required:
                    normalized[name] = None
                    continue
                normalized[name] = cls.validate_and_normalize_output(item, item_schema, error_type, f'{path}.{name}')
            return normalized
        if isinstance(value, list) and isinstance(schema.get('items'), dict):
            return [
                cls.validate_and_normalize_output(item, schema['items'], error_type, f'{path}[{index}]')
                for index, item in enumerate(value)
            ]
        return value

    @staticmethod
    def _matches_type(value, expected_type, error_type=ValueError):
        type_checks = {
            'object': lambda item: isinstance(item, dict),
            'array': lambda item: isinstance(item, list),
            'string': lambda item: isinstance(item, str),
            'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
            'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            'boolean': lambda item: isinstance(item, bool),
            'null': lambda item: item is None,
        }
        try:
            return type_checks[expected_type](value)
        except KeyError as error:
            raise error_type(f'Unsupported JSON Schema type: {expected_type}') from error

    @classmethod
    def to_strict_response_schema(cls, schema):
        """Adapt a phase schema for the Responses API structured-output format.

        The API can use strict mode only when every declared property, including
        nested properties, is required. This method detects that condition,
        validates and normalizes the schema tree, and then either sets
        ``additionalProperties: false`` throughout the tree or removes that
        restriction when optional fields require non-strict mode.

        Example using the effective ``github_issue_added`` Analysis schema::

            # The framework fields analysis_report, recommended_actions, and
            # skip_handling are required. The skill fields original_issue_type
            # and classified_issue_type also default to required.
            schema['required'] == [
                'analysis_report',
                'recommended_actions',
                'skip_handling',
                'original_issue_type',
                'classified_issue_type',
            ]

            # Because all five properties are required, the API receives:
            response_schema['strict'] is True
            response_schema['schema']['additionalProperties'] is False

            # The skill restriction is preserved during adaptation:
            response_schema['schema']['properties'][
                'recommended_actions'
            ]['items']['enum'] == [
                'suggest_modify_type',
                'suggest_assign_labels',
                'suggest_reply',
                'suggest_create_ticket',
                'no_op',
            ]

            # If a skill field declared required: false, strict would be False
            # and additionalProperties would be removed from every object node.
        """
        if not isinstance(schema, dict):
            raise TypeError('schema must be an object')
        schema = deepcopy(schema)
        strict = cls._all_properties_required(schema)
        schema = cls._normalize_strict_response_schema(schema)
        cls._apply_strict_additional_properties(schema, strict)
        return {
            'schema': schema,
            'strict': strict,
        }

    @classmethod
    def _normalize_strict_response_schema(cls, schema):
        """Recursively validate object schemas before API submission.

        Traversal covers composition branches, array items, and nested object
        properties while preserving all schema constraints.
        """
        for key in ('allOf', 'anyOf', 'oneOf'):
            if isinstance(schema.get(key), list):
                schema[key] = [
                    cls._normalize_strict_response_schema(item) if isinstance(item, dict) else item
                    for item in schema[key]
                ]
        if isinstance(schema.get('items'), dict):
            schema['items'] = cls._normalize_strict_response_schema(schema['items'])
        properties = schema.get('properties')
        if not isinstance(properties, dict):
            return schema
        required = schema.get('required') or []
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            raise ValueError('schema required fields must be a string array')
        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                raise ValueError(f'schema property {name!r} must be an object')
            properties[name] = cls._normalize_strict_response_schema(property_schema)
        return schema

    @classmethod
    def _all_properties_required(cls, schema):
        """Return whether every property in the complete schema tree is required."""
        for key in ('allOf', 'anyOf', 'oneOf'):
            if any(
                isinstance(item, dict) and not cls._all_properties_required(item)
                for item in schema.get(key, [])
            ):
                return False
        if isinstance(schema.get('items'), dict) and not cls._all_properties_required(schema['items']):
            return False
        properties = schema.get('properties')
        if not isinstance(properties, dict):
            return True
        required = schema.get('required') or []
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            raise ValueError('schema required fields must be a string array')
        if not set(properties).issubset(required):
            return False
        return all(cls._all_properties_required(property_schema)
                   for property_schema in properties.values())

    @classmethod
    def _apply_strict_additional_properties(cls, schema, strict):
        """Apply the selected strictness policy to every object schema node."""
        for key in ('allOf', 'anyOf', 'oneOf'):
            for item in schema.get(key, []):
                if isinstance(item, dict):
                    cls._apply_strict_additional_properties(item, strict)
        if isinstance(schema.get('items'), dict):
            cls._apply_strict_additional_properties(schema['items'], strict)
        properties = schema.get('properties')
        if not isinstance(properties, dict):
            return
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                cls._apply_strict_additional_properties(property_schema, strict)
        if strict:
            schema['additionalProperties'] = False
        else:
            schema.pop('additionalProperties', None)
