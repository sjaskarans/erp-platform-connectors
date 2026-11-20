import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from erp_connectors.demo_assets import generate
from erp_connectors.demo_data import TABLES, seed_sql
from erp_connectors.local_demo import LocalDemoConnector


class DemoDataTests(unittest.TestCase):
    def setUp(self):
        self.connector = LocalDemoConnector()

    def tearDown(self):
        self.connector.close()

    def test_all_rows_seeded_and_leading_zero_ids_preserved(self):
        self.assertEqual(len(self.connector.discover()), 9)
        for name, spec in TABLES.items():
            self.assertEqual(len(self.connector.preview(name, 100).rows), len(spec.rows))
        self.assertEqual(self.connector.preview("customers", 1).rows[0][0], "000001")

    def test_all_reference_queries_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            generate("erp_demo", "erp_connector_demo_v1", Path(directory))
            expected = json.loads((Path(directory) / "expected.json").read_text())
        for name, rows in expected["company_1000"].items():
            params = {"company_code": "1000"}
            if name.startswith("overdue_"):
                params["as_of_date"] = "2026-11-18"
            actual = self.connector.execute(name, params).rows
            self.assertEqual(len(actual), len(rows), name)
            for received, target in zip(actual, rows):
                self.assertEqual(received[0], target[0], name)
                self.assertEqual([Decimal(v) for v in received[1:]], [Decimal(v) for v in target[1:]], name)

    def test_company_filter_does_not_mix_erp_companies(self):
        result = self.connector.execute("finance_by_currency", {"company_code": "2000"})
        self.assertEqual(result.rows, [["USD", "1900.00"]])

    def test_injection_is_a_bound_value(self):
        result = self.connector.execute("finance_by_currency", {"company_code": "1000' OR 1=1 --"})
        self.assertEqual(result.rows, [])

    def test_inventory_missing_cost_is_reported(self):
        result = self.connector.execute("inventory_valuation", {"company_code": "1000"})
        self.assertEqual(result.rows[0][2], "1")

    def test_seed_does_not_replace_existing_objects(self):
        sql = seed_sql("erp_demo", "erp_connector_demo_v1")
        self.assertNotIn("OR REPLACE", sql)
        self.assertNotIn("DROP ", sql)
        self.assertNotIn("IF NOT EXISTS", sql)
        self.assertEqual(sql.count("CREATE TABLE"), 9)
        self.assertEqual(sql.count("INSERT INTO"), 9)

    def test_seed_rejects_invalid_names_and_non_demo_schema(self):
        for catalog, schema in (("test;DROP", "erp_connector_demo_v1"), ("catalog", "production")):
            with self.assertRaises(ValueError):
                seed_sql(catalog, schema)

    def test_unknown_and_extra_parameters_rejected(self):
        with self.assertRaises(KeyError):
            self.connector.execute("raw_sql", {})
        with self.assertRaises(ValueError):
            self.connector.execute("finance_by_currency", {"company_code": "1000", "sql": "SELECT 1"})

    def test_generated_snowflake_queries_reconcile_against_same_fixture(self):
        from erp_connectors.snowflake import parameter
        with tempfile.TemporaryDirectory() as directory:
            generate("ERP_DEMO", "erp_connector_demo_v1", Path(directory), "snowflake")
            config = json.loads((Path(directory) / "connector.demo.json").read_text())
            expected = json.loads((Path(directory) / "expected.json").read_text())
            sql = (Path(directory) / "setup.sql").read_text()
        self.assertEqual(sql.count("CREATE TABLE"), 9)
        self.assertNotIn("USING DELTA", sql)
        for name, query in config["queries"].items():
            self.assertNotIn(":company_code", query["statement"])
            values = {"company_code": "1000", "as_of_date": "2026-11-18"}
            bindings = [parameter(values[p["name"]], p["type"]) for p in query["parameters"]]
            rows = self.connector.db.execute(query["statement"], bindings).fetchall()
            reference = expected["company_1000"][name]
            self.assertEqual(len(rows), len(reference))
            for actual, target in zip(rows, reference):
                self.assertEqual(actual[0], target[0])
                self.assertEqual([Decimal(str(v)) for v in actual[1:]], [Decimal(v) for v in target[1:]])

    def test_generated_redshift_fixture_and_queries_reconcile_offline(self):
        import duckdb
        import re
        from erp_connectors.connections import RedshiftConnectionInput
        with tempfile.TemporaryDirectory() as directory:
            generate('dev', 'erp_connector_demo_v1', Path(directory), 'redshift')
            config = json.loads((Path(directory) / 'connector.demo.json').read_text())
            expected = json.loads((Path(directory) / 'expected.json').read_text())
            sql = (Path(directory) / 'setup.sql').read_text()
            grants = (Path(directory) / 'grants.template.sql').read_text()
        profile = RedshiftConnectionInput.model_validate({k: v for k, v in config.items() if k not in {'queries', 'limits'}})
        self.assertEqual(len(profile.objects), 9)
        self.assertNotIn('DROP ', sql)
        self.assertNotIn('OR REPLACE', sql)
        self.assertNotIn('ON FUTURE', grants)
        db = duckdb.connect(':memory:')
        try:
            db.execute(sql)
            for name, count in expected['row_counts'].items():
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM erp_connector_demo_v1."{name}"').fetchone()[0], count)
            self.assertEqual(db.execute('SELECT customer_id FROM erp_connector_demo_v1.customers LIMIT 1').fetchone()[0], '000001')
            for name, query in config['queries'].items():
                values = {'company_code': '1000', 'as_of_date': '2026-11-18'}
                bindings = []
                def bind(match):
                    bindings.append(values[match.group(1)])
                    return '?'
                statement = re.sub(r':([A-Za-z_][A-Za-z0-9_]*)', bind, query['statement'])
                rows = db.execute(statement, bindings).fetchall()
                reference = expected['company_1000'][name]
                self.assertEqual(len(rows), len(reference), name)
                for row, target in zip(rows, reference):
                    self.assertEqual(row[0], target[0], name)
                    self.assertEqual([Decimal(str(v)) for v in row[1:]], [Decimal(v) for v in target[1:]], name)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
