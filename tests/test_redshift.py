import copy
import io
import json
import logging
import unittest

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.stub import ANY, Stubber

from erp_connectors import ConnectorError, DataObject, Parameter, QueryTemplate, RedshiftConnector
from erp_connectors.redshift import cell, parameter, quote_identifier

STATEMENT = "11111111-1111-1111-1111-111111111111"
META = [{"name": "amount", "typeName": "numeric", "precision": 38, "scale": 6, "nullable": 1}]


class Client:
    def __init__(self, pages=None, states=None, expected=1, error=None):
        self.pages = copy.deepcopy(pages if pages is not None else [{"ColumnMetadata": META,
            "Records": [[{"stringValue": "9007199254740993.123456"}]], "TotalNumRows": 1}])
        self.states = list(states or ["FINISHED"])
        self.expected, self.error = expected, error
        self.calls, self.results, self.cancelled = [], [], []
        self.closed = False

    def execute_statement(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return {"Id": STATEMENT}

    def describe_statement(self, **kwargs):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"Id": STATEMENT, "Status": state, "HasResultSet": True,
                "ResultRows": self.expected, "Error": "private-query-or-binding"}

    def get_statement_result(self, **kwargs):
        self.results.append(kwargs)
        return self.pages.pop(0)

    def cancel_statement(self, **kwargs):
        self.cancelled.append(kwargs)
        return {"Status": True}

    def close(self): self.closed = True


def connector(client, **kwargs):
    return RedshiftConnector(client, "dev", {
        "finance": DataObject("dev", "erp_connector_demo_v1", "finance_lines", ("amount",)),
        "no_preview": DataObject("dev", "erp_connector_demo_v1", "finance_lines"),
        "other_db": DataObject("other", "approved", "finance_lines", ("amount",)),
    }, {"total": QueryTemplate("SELECT SUM(amount) FROM approved.finance_lines WHERE company_code = :company",
                              (Parameter("company", "STRING"),))}, workgroup_name="erp-test", **kwargs)


class RedshiftTests(unittest.TestCase):
    def test_financial_precision_and_metadata(self):
        result = connector(Client()).execute("total", {"company": "1000"})
        self.assertEqual(result.rows, [["9007199254740993.123456"]])
        self.assertEqual(result.columns[0]["scale"], 6)
        self.assertEqual(result.platform, "redshift")
        self.assertEqual(result.statement_id, STATEMENT)

    def test_named_binding_and_idempotency_token(self):
        client = Client()
        connector(client).execute("total", {"company": "1000' OR 1=1 --"})
        call = client.calls[0]
        self.assertNotIn("1000' OR", call['Sql'])
        self.assertEqual(call['Parameters'], [{'name': 'company', 'value': "1000' OR 1=1 --"}])
        self.assertEqual(call['Database'], 'dev')
        self.assertEqual(call['WorkgroupName'], 'erp-test')
        self.assertEqual(len(call['ClientToken']), 36)
        self.assertNotIn('DbUser', call)
        self.assertNotIn('SecretArn', call)

    def test_all_result_pages_are_fetched_and_metadata_retained(self):
        client = Client(pages=[{'ColumnMetadata': META, 'Records': [[{'stringValue': '1.00'}]],
                              'NextToken': 'page-2', 'TotalNumRows': 2},
                             {'ColumnMetadata': [], 'Records': [[{'stringValue': '2.00'}]], 'TotalNumRows': 2}], expected=2)
        result = connector(client).check_connection()
        self.assertEqual(result.rows, [['1.00'], ['2.00']])
        self.assertEqual(client.results, [{'Id': STATEMENT}, {'Id': STATEMENT, 'NextToken': 'page-2'}])

    def test_empty_result_preserves_columns(self):
        result = connector(Client([{'ColumnMetadata': META, 'Records': [], 'TotalNumRows': 0}], expected=0)).check_connection()
        self.assertEqual(result.rows, [])
        self.assertEqual(result.columns[0]['name'], 'amount')

    def test_unknown_describe_count_uses_result_total(self):
        self.assertEqual(len(connector(Client(expected=-1)).check_connection().rows), 1)

    def test_malformed_or_incomplete_results_fail_closed(self):
        cases = [
            ([{'ColumnMetadata': META, 'Records': [[]], 'TotalNumRows': 1}], 1, 'INVALID_ROW_WIDTH'),
            ([{'Records': [[{'stringValue': '1'}]], 'TotalNumRows': 1}], 1, 'MISSING_RESULT_METADATA'),
            ([{'ColumnMetadata': META, 'Records': [], 'TotalNumRows': 1}], 1, 'RESULT_ROW_COUNT_MISMATCH'),
            ([{'ColumnMetadata': META, 'Records': [], 'TotalNumRows': 2}], 1, 'RESULT_ROW_COUNT_MISMATCH'),
            ([{'ColumnMetadata': META, 'Records': [[{'doubleValue': 1.1}]], 'TotalNumRows': 1}], 1, 'DECIMAL_PRECISION_UNSAFE'),
        ]
        for pages, count, code in cases:
            with self.subTest(code=code), self.assertRaises(ConnectorError) as caught:
                connector(Client(pages, expected=count)).check_connection()
            self.assertEqual(caught.exception.code, code)

    def test_metadata_changes_are_rejected(self):
        client = Client([{'ColumnMetadata': META, 'Records': [], 'NextToken': 'two'},
                         {'ColumnMetadata': [{'name': 'other', 'typeName': 'varchar'}], 'Records': []}], expected=0)
        with self.assertRaisesRegex(ConnectorError, 'INCONSISTENT_RESULT_METADATA'):
            connector(client).check_connection()

    def test_repeated_page_tokens_fail(self):
        client = Client([{'ColumnMetadata': META, 'Records': [], 'NextToken': 'same'},
                         {'Records': [], 'NextToken': 'same'}], expected=0)
        with self.assertRaisesRegex(ConnectorError, 'INVALID_RESULT_PAGINATION'):
            connector(client).check_connection()

    def test_page_budget_is_enforced(self):
        client = Client([{'ColumnMetadata': META, 'Records': [], 'NextToken': 'two'}], expected=0)
        with self.assertRaisesRegex(ConnectorError, 'RESULT_PAGE_LIMIT_EXCEEDED'):
            connector(client, max_pages=1).check_connection()

    def test_row_and_byte_budgets_do_not_return_partial_results(self):
        with self.assertRaisesRegex(ConnectorError, 'RESULT_LIMIT_EXCEEDED'):
            connector(Client(expected=2), max_rows=1).check_connection()
        client = Client([{'ColumnMetadata': META, 'Records': [[{'stringValue': 'x' * 2000}]]}])
        with self.assertRaisesRegex(ConnectorError, 'RESULT_LIMIT_EXCEEDED'):
            connector(client, max_bytes=1024).check_connection()

    def test_timeout_cancels_pending_statement(self):
        now, client = [0.0], Client(states=['STARTED'])
        with self.assertRaisesRegex(ConnectorError, 'TIMEOUT'):
            connector(client, timeout_seconds=1, clock=lambda: now[0],
                      sleep=lambda value: now.__setitem__(0, now[0] + value)).check_connection()
        self.assertEqual(client.cancelled, [{'Id': STATEMENT}])

    def test_terminal_sql_failure_does_not_expose_error_or_cancel(self):
        client = Client(states=['FAILED'])
        with self.assertRaises(ConnectorError) as caught:
            connector(client).check_connection()
        self.assertEqual(caught.exception.code, 'REDSHIFT_STATEMENT_FAILED')
        self.assertNotIn('private-query-or-binding', str(caught.exception))
        self.assertEqual(client.cancelled, [])

    def test_safe_aws_error_categories(self):
        for error, expected in [
            (NoCredentialsError(), 'AWS_CREDENTIALS_UNAVAILABLE'),
            (ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'private-token'}}, 'ExecuteStatement'), 'AWS_ACCESS_DENIED'),
            (ClientError({'Error': {'Code': 'ThrottlingException', 'Message': 'private-token'}}, 'ExecuteStatement'), 'AWS_THROTTLED'),
        ]:
            with self.subTest(expected=expected), self.assertRaises(ConnectorError) as caught:
                connector(Client(error=error)).check_connection()
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn('private-token', str(caught.exception))

    def test_preflight_rejections_do_not_submit_sql(self):
        client, api = Client(), None
        api = connector(client)
        for action in [lambda: api.execute('unknown', {}), lambda: api.execute('total', {'wrong': '1'}),
                       lambda: api.execute('total', {'company': None}), lambda: api.execute('total', {'company': ''}),
                       lambda: api.preview('no_preview'), lambda: api.preview('finance', True),
                       lambda: api.preview('other_db'), lambda: api.browse('objects', database='other', schema_name='s')]:
            with self.assertRaises((ValueError, KeyError)): action()
        self.assertEqual(client.calls, [])

    def test_metadata_values_are_bound(self):
        client = Client([{'ColumnMetadata': [{'name': 'table_name', 'typeName': 'varchar'}, {'name': 'table_type', 'typeName': 'varchar'}],
                          'Records': [[{'stringValue': 'finance'}, {'stringValue': 'BASE TABLE'}]]}], expected=1)
        page = connector(client).browse('objects', database='dev', schema_name="s' OR 1=1 --", prefix='fin', after='f')
        call = client.calls[0]
        self.assertNotIn("s' OR", call['Sql'])
        self.assertEqual(dict((p['name'], p['value']) for p in call['Parameters'])['schema_name'], "s' OR 1=1 --")
        self.assertEqual(page['items'], [{'name': 'finance', 'kind': 'TABLE'}])

    def test_metadata_keyset_pagination_retains_lookahead(self):
        client = Client([{'ColumnMetadata': [{'name': 'schema_name', 'typeName': 'varchar'}],
                          'Records': [[{'stringValue': f's{i:03}'}] for i in range(101)]}], expected=101)
        page = connector(client).browse('schemas', database='dev')
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(page['next_cursor'], 's099')

    def test_database_browser_is_scoped_to_connected_database(self):
        client = Client([{'ColumnMetadata': [{'name': 'database_name', 'typeName': 'varchar'}],
                          'Records': [[{'stringValue': 'dev'}]]}])
        self.assertEqual(connector(client).browse('databases')['items'], [{'name': 'dev'}])

    def test_describe_binds_exact_object_and_preview_uses_explicit_columns(self):
        client = Client()
        connector(client).describe('finance')
        self.assertIn('information_schema.columns', client.calls[0]['Sql'])
        self.assertEqual(dict((p['name'], p['value']) for p in client.calls[0]['Parameters'])['table_name'], 'finance_lines')
        client = Client()
        connector(client).preview('finance', 5)
        self.assertEqual(client.calls[0]['Sql'], 'SELECT "amount" FROM "erp_connector_demo_v1"."finance_lines" LIMIT 5')

    def test_identifiers_and_parameter_types(self):
        self.assertEqual(quote_identifier('a"b'), '"a""b"')
        with self.assertRaises(ValueError): quote_identifier('x' * 128)
        with self.assertRaises(ValueError): quote_identifier('é' * 64)
        self.assertEqual(parameter('9007199254740993.123456', 'DECIMAL'), '9007199254740993.123456')
        self.assertEqual(parameter('2026-11-20', 'DATE'), '2026-11-20')
        self.assertEqual(parameter('TRUE', 'BOOLEAN'), 'true')
        for value, kind in [('NaN', 'DECIMAL'), ('Infinity', 'DECIMAL'), ('1e10000', 'DECIMAL'), ('x', 'INT'), ('9223372036854775808', 'INT')]:
            with self.assertRaises(ValueError): parameter(value, kind)

    def test_result_scalar_types_preserve_null_integer_and_binary(self):
        self.assertIsNone(cell({'isNull': True}, 'varchar'))
        self.assertEqual(cell({'longValue': 9007199254740993}, 'int8'), '9007199254740993')
        self.assertEqual(cell({'booleanValue': False}, 'bool'), 'false')
        self.assertEqual(cell({'blobValue': b'abc'}, 'varbyte'), 'YWJj')
        for value in [{'isNull': False}, {'doubleValue': float('nan')}, {'unknown': 'private'}, {'stringValue': 'a', 'longValue': 1}]:
            with self.assertRaises(ConnectorError): cell(value, 'varchar')

    def test_audit_excludes_query_parameters_and_rows(self):
        stream, logger = io.StringIO(), logging.getLogger('erp_connectors.redshift')
        handler, previous = logging.StreamHandler(stream), logger.level
        logger.setLevel(logging.INFO); logger.addHandler(handler)
        try: connector(Client()).execute('total', {'company': 'sensitive-company'})
        finally: logger.removeHandler(handler); logger.setLevel(previous)
        audit = json.loads(stream.getvalue())
        self.assertEqual(audit['outcome'], 'SUCCEEDED')
        for secret in ['sensitive-company', '9007199254740993', 'SELECT SUM']:
            self.assertNotIn(secret, stream.getvalue())

    def test_close_releases_sdk_client(self):
        client = Client(); connector(client).close()
        self.assertTrue(client.closed)


class AWSDataAPIContractTests(unittest.TestCase):
    def test_actual_sdk_accepts_both_iam_target_shapes_and_named_parameters(self):
        for target in [{'workgroup_name': 'erp-test'}, {'cluster_identifier': 'erp-cluster'}]:
            with self.subTest(target=target):
                client = boto3.client('redshift-data', region_name='us-east-1',
                    aws_access_key_id='offline-test', aws_secret_access_key='offline-test')
                self.addCleanup(client.close)
                request = {'WorkgroupName': 'erp-test'} if 'workgroup_name' in target else {'ClusterIdentifier': 'erp-cluster'}
                request.update(Database='dev', Sql='SELECT CAST(:value AS DECIMAL(38,6)) AS amount',
                               ClientToken=ANY, ResultFormat='JSON', Parameters=[{'name': 'value', 'value': '123.450000'}])
                with Stubber(client) as stub:
                    stub.add_response('execute_statement', {'Id': STATEMENT}, request)
                    stub.add_response('describe_statement', {'Id': STATEMENT, 'Status': 'FINISHED', 'HasResultSet': True, 'ResultRows': 1}, {'Id': STATEMENT})
                    stub.add_response('get_statement_result', {'ColumnMetadata': META, 'Records': [[{'stringValue': '123.450000'}]], 'TotalNumRows': 1}, {'Id': STATEMENT})
                    api = RedshiftConnector(client, 'dev', {}, {'q': QueryTemplate(request['Sql'], (Parameter('value', 'DECIMAL'),))}, **target)
                    self.assertEqual(api.execute('q', {'value': '123.450000'}).rows, [['123.450000']])
                    stub.assert_no_pending_responses()


if __name__ == '__main__': unittest.main()
