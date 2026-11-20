-- Run as an administrator in the configured Redshift database.
-- Replace IAMR:ERPConnectorRole with the exact database identity returned by the connection check.
-- Create the role only if it does not already exist; stop on errors.
CREATE ROLE erp_connector_reader;
GRANT USAGE ON SCHEMA "erp_connector_demo_v1" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."companies" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."customers" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."suppliers" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."finance_lines" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."receivables" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."payables" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."inventory_positions" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."sales_orders" TO ROLE erp_connector_reader;
GRANT SELECT ON TABLE "erp_connector_demo_v1"."purchase_orders" TO ROLE erp_connector_reader;
GRANT ROLE erp_connector_reader TO "IAMR:ERPConnectorRole";
-- No CREATE/write privileges, future grants, or changes to PUBLIC are applied here.
