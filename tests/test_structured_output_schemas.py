import json
import os
import unittest


_TEST_MODEL = {
    'type': 'hosted_vllm',
    'model': 'test-model',
    'url': 'http://localhost:8000/v1',
    'default': True,
}
os.environ['LLM_MODELS'] = json.dumps([_TEST_MODEL])
os.environ['EMBEDDING_MODEL'] = json.dumps(_TEST_MODEL)
os.environ['AGENT'] = '{}'

from seaqa_ai.agent.models.phase.prelude.spec import PreludePhaseSpec
from seaqa_ai.agent.utils.final_output_schema_utils import FinalOutputSchemaUtils
from seaqa_ai.agent.utils.record_identity import record_identity_schema


class StructuredOutputSchemaTests(unittest.TestCase):
    def test_prelude_record_identities_use_supported_any_of(self):
        response_schema = FinalOutputSchemaUtils.to_strict_response_schema(
            PreludePhaseSpec.base_final_output_schema,
        )['schema']
        items = response_schema['properties']['event_related_records']['items']

        self.assertEqual(set(items), {'anyOf'})
        self.assertEqual(len(items['anyOf']), 2)
        self.assertNotIn('allOf', str(response_schema))
        self.assertNotIn("'if'", str(response_schema))
        self.assertNotIn("'then'", str(response_schema))
        self.assertNotIn("'else'", str(response_schema))

        extra_source, connection_source = items['anyOf']
        self.assertEqual(extra_source['properties']['connection_id']['type'], 'null')
        self.assertEqual(connection_source['properties']['connection_id']['type'], 'integer')
        for branch in items['anyOf']:
            self.assertEqual(
                branch['required'], ['source_type', 'connection_id', 'record_id'],
            )
            self.assertFalse(branch['additionalProperties'])

    def test_any_of_record_identity_validation_enforces_connection_id_type(self):
        schema = record_identity_schema(nested=True)

        self.assertEqual(
            FinalOutputSchemaUtils.validate_and_normalize_output(
                {
                    'source_type': 'ticket',
                    'connection_id': None,
                    'record_id': 1,
                },
                schema,
                ValueError,
            ),
            {'source_type': 'ticket', 'connection_id': None, 'record_id': 1},
        )
        self.assertEqual(
            FinalOutputSchemaUtils.validate_and_normalize_output(
                {
                    'source_type': 'github_issue',
                    'connection_id': 1,
                    'record_id': 2,
                },
                schema,
                ValueError,
            ),
            {'source_type': 'github_issue', 'connection_id': 1, 'record_id': 2},
        )

        with self.assertRaisesRegex(ValueError, 'must match one anyOf schema'):
            FinalOutputSchemaUtils.validate_and_normalize_output(
                {
                    'source_type': 'ticket',
                    'connection_id': 1,
                    'record_id': 1,
                },
                schema,
                ValueError,
            )

        with self.assertRaisesRegex(ValueError, 'must match one anyOf schema'):
            FinalOutputSchemaUtils.validate_and_normalize_output(
                {
                    'source_type': 'github_issue',
                    'connection_id': None,
                    'record_id': 2,
                },
                schema,
                ValueError,
            )

    def test_root_record_identity_schema_is_an_object(self):
        schema = record_identity_schema()

        self.assertEqual(schema['type'], 'object')
        self.assertNotIn('anyOf', schema)
        self.assertNotIn('allOf', schema)
