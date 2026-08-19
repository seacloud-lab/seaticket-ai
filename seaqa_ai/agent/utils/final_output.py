from copy import deepcopy


class FinalOutputSchema:
    """Build, validate, and adapt phase final-output JSON Schemas."""

    @classmethod
    def build(cls, default_schema, additional_output, event, phase):
        cls._validate_additional_output(additional_output, event, phase, default_schema)
        schema = deepcopy(default_schema)
        properties = schema.setdefault('properties', {})
        for name, property_schema in additional_output.items():
            is_default_field = name in properties
            output_schema = {
                key: value
                for key, value in property_schema.items()
                if key != 'required'
            }
            if is_default_field:
                properties[name] = cls._merge(properties[name], output_schema)
            else:
                properties[name] = output_schema
                if property_schema.get('required', True):
                    schema.setdefault('required', []).append(name)
        return schema if properties else {}

    @staticmethod
    def additional_fields(default_schema, additional_output):
        default_fields = default_schema.get('properties', {})
        return set(additional_output) - set(default_fields)

    @classmethod
    def _validate_additional_output(cls, additional_output, event, phase, default_schema):
        if additional_output is None:
            return
        if not isinstance(additional_output, dict):
            raise ValueError(f'Skill {event}/{phase} additional_output must be an object')
        if not additional_output:
            raise ValueError(f'Skill {event}/{phase} additional_output must not be empty')

        default_properties = default_schema.get('properties', {})
        for name, property_schema in additional_output.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f'Skill {event}/{phase} additional_output property names must be strings')
            if not isinstance(property_schema, dict):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} must be an object'
                )
            is_update = name in default_properties
            if is_update:
                cls._validate_update(default_properties[name], property_schema, event, phase, name)
            if not is_update and not isinstance(property_schema.get('type'), str):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} requires a type'
                )
            if not is_update and (
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
            if is_update and 'required' in property_schema:
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} cannot change required'
                )
            if required and 'default' in property_schema:
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} default requires required: false'
                )
            effective_schema = (
                cls._merge(default_properties[name], property_schema)
                if is_update else property_schema
            )
            cls._validate_enums(effective_schema, event, phase, name)
            if 'default' in property_schema and not cls._value_matches_schema(
                property_schema['default'], effective_schema,
            ):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} default does not match its schema'
                )

    @classmethod
    def _merge(cls, default_schema, updates):
        schema = deepcopy(default_schema)
        for key, value in updates.items():
            if key == 'required':
                continue
            if isinstance(schema.get(key), dict) and isinstance(value, dict):
                schema[key] = cls._merge(schema[key], value)
            else:
                schema[key] = deepcopy(value)
        return schema

    @classmethod
    def _validate_update(cls, default_schema, updates, event, phase, name):
        if not isinstance(default_schema, dict) or not isinstance(updates, dict):
            return
        if 'type' in updates and updates['type'] != default_schema.get('type'):
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} cannot change type'
            )
        for key, value in updates.items():
            if isinstance(default_schema.get(key), dict) and isinstance(value, dict):
                cls._validate_update(default_schema[key], value, event, phase, name)

    @classmethod
    def _validate_enums(cls, schema, event, phase, name):
        if not isinstance(schema, dict):
            return
        enum = schema.get('enum')
        if enum is not None:
            if not isinstance(enum, list):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} enum must be an array'
                )
            schema_without_enum = {key: value for key, value in schema.items() if key != 'enum'}
            if any(not cls._value_matches_schema(value, schema_without_enum) for value in enum):
                raise ValueError(
                    f'Skill {event}/{phase} additional_output property {name!r} enum does not match its schema'
                )
        if isinstance(schema.get('items'), dict):
            cls._validate_enums(schema['items'], event, phase, name)
        for property_schema in (schema.get('properties') or {}).values():
            cls._validate_enums(property_schema, event, phase, name)

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
    def validate_output(cls, value, schema, error_type, path='$'):
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
                normalized[name] = cls.validate_output(item, item_schema, error_type, f'{path}.{name}')
            return normalized
        if isinstance(value, list) and isinstance(schema.get('items'), dict):
            return [
                cls.validate_output(item, schema['items'], error_type, f'{path}[{index}]')
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
        if not isinstance(schema, dict):
            raise TypeError('schema must be an object')
        schema = deepcopy(schema)
        strict = cls._has_only_required_properties(schema)
        schema = cls._convert_to_strict_response_schema(schema)
        cls._set_additional_properties(schema, strict)
        return {
            'schema': schema,
            'strict': strict,
        }

    @classmethod
    def _convert_to_strict_response_schema(cls, schema):
        for key in ('allOf', 'anyOf', 'oneOf'):
            if isinstance(schema.get(key), list):
                schema[key] = [
                    cls._convert_to_strict_response_schema(item) if isinstance(item, dict) else item
                    for item in schema[key]
                ]
        if isinstance(schema.get('items'), dict):
            schema['items'] = cls._convert_to_strict_response_schema(schema['items'])
        properties = schema.get('properties')
        if not isinstance(properties, dict):
            return schema
        required = schema.get('required') or []
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            raise ValueError('schema required fields must be a string array')
        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                raise ValueError(f'schema property {name!r} must be an object')
            properties[name] = cls._convert_to_strict_response_schema(property_schema)
        return schema

    @classmethod
    def _has_only_required_properties(cls, schema):
        for key in ('allOf', 'anyOf', 'oneOf'):
            if any(
                isinstance(item, dict) and not cls._has_only_required_properties(item)
                for item in schema.get(key, [])
            ):
                return False
        if isinstance(schema.get('items'), dict) and not cls._has_only_required_properties(schema['items']):
            return False
        properties = schema.get('properties')
        if not isinstance(properties, dict):
            return True
        required = schema.get('required') or []
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            raise ValueError('schema required fields must be a string array')
        if not set(properties).issubset(required):
            return False
        return all(cls._has_only_required_properties(property_schema)
                   for property_schema in properties.values())

    @classmethod
    def _set_additional_properties(cls, schema, strict):
        for key in ('allOf', 'anyOf', 'oneOf'):
            for item in schema.get(key, []):
                if isinstance(item, dict):
                    cls._set_additional_properties(item, strict)
        if isinstance(schema.get('items'), dict):
            cls._set_additional_properties(schema['items'], strict)
        properties = schema.get('properties')
        if not isinstance(properties, dict):
            return
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                cls._set_additional_properties(property_schema, strict)
        if strict:
            schema['additionalProperties'] = False
        else:
            schema.pop('additionalProperties', None)
