-- Synthetic Redshift fixture: run as a setup owner in the configured database.

-- Stop on the first error; use a fresh schema, never replace existing client data.

CREATE SCHEMA "erp_connector_demo_v1";

CREATE TABLE "erp_connector_demo_v1"."companies" ("company_code" VARCHAR(256), "source_system" VARCHAR(256), "name" VARCHAR(256), "currency" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."companies" VALUES
('1000', 'SAP_DEMO', 'Demo India', 'INR'),
('2000', 'DYNAMICS_DEMO', 'Demo US', 'USD'),
('3000', 'SAP_DEMO', 'Demo UK', 'GBP');

CREATE TABLE "erp_connector_demo_v1"."customers" ("customer_id" VARCHAR(256), "company_code" VARCHAR(256), "name" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."customers" VALUES
('000001', '1000', 'Fictional India Customer'),
('000001', '2000', 'Fictional US Customer'),
('000003', '3000', 'Fictional UK Customer');

CREATE TABLE "erp_connector_demo_v1"."suppliers" ("supplier_id" VARCHAR(256), "company_code" VARCHAR(256), "name" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."suppliers" VALUES
('000010', '1000', 'Fictional India Supplier'),
('000010', '2000', 'Fictional US Supplier');

CREATE TABLE "erp_connector_demo_v1"."finance_lines" ("company_code" VARCHAR(256), "fiscal_year" INT, "document_id" VARCHAR(256), "account" VARCHAR(256), "amount" DECIMAL(18,2), "currency" VARCHAR(256), "posting_date" DATE);

INSERT INTO "erp_connector_demo_v1"."finance_lines" VALUES
('1000', 2026, '000001', 'REVENUE', 1000.00, 'INR', DATE '2026-10-01'),
('1000', 2026, '000002', 'REVENUE_REVERSAL', -200.00, 'INR', DATE '2026-10-02'),
('1000', 2026, '000003', 'COGS', -600.00, 'INR', DATE '2026-10-03'),
('1000', 2026, '000004', 'REVENUE', 50.00, 'USD', DATE '2026-10-03'),
('2000', 2026, '000001', 'REVENUE', 2000.00, 'USD', DATE '2026-10-01'),
('2000', 2026, '000002', 'REVENUE_REVERSAL', -100.00, 'USD', DATE '2026-10-02');

CREATE TABLE "erp_connector_demo_v1"."receivables" ("company_code" VARCHAR(256), "invoice_id" VARCHAR(256), "customer_id" VARCHAR(256), "due_date" DATE, "outstanding_amount" DECIMAL(18,2), "currency" VARCHAR(256), "disputed" BOOLEAN);

INSERT INTO "erp_connector_demo_v1"."receivables" VALUES
('1000', 'INV001', '000001', DATE '2026-10-15', 600.00, 'INR', FALSE),
('1000', 'INV002', '000001', DATE '2026-10-20', 400.00, 'INR', TRUE),
('1000', 'INV003', '000001', DATE '2026-12-01', 200.00, 'INR', FALSE),
('1000', 'INV004', '000001', DATE '2026-10-15', 50.00, 'USD', FALSE),
('2000', 'INV001', '000001', DATE '2026-10-15', 800.00, 'USD', FALSE),
('2000', 'INV002', '000001', DATE '2026-10-15', 0.00, 'USD', FALSE),
('3000', 'INV001', '000003', DATE '2026-10-15', 250.00, 'GBP', FALSE);

CREATE TABLE "erp_connector_demo_v1"."payables" ("company_code" VARCHAR(256), "invoice_id" VARCHAR(256), "supplier_id" VARCHAR(256), "due_date" DATE, "outstanding_amount" DECIMAL(18,2), "currency" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."payables" VALUES
('1000', 'BILL001', '000010', DATE '2026-10-15', 300.00, 'INR'),
('1000', 'BILL002', '000010', DATE '2026-12-01', 150.00, 'INR'),
('2000', 'BILL001', '000010', DATE '2026-10-15', 500.00, 'USD');

CREATE TABLE "erp_connector_demo_v1"."inventory_positions" ("company_code" VARCHAR(256), "material_id" VARCHAR(256), "quantity" DECIMAL(18,2), "unit_cost" DECIMAL(18,2), "currency" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."inventory_positions" VALUES
('1000', '000100', 10.00, 25.00, 'INR'),
('1000', '000101', 2.00, NULL, 'INR'),
('2000', '000100', 5.00, 40.00, 'USD'),
('3000', '000100', 3.00, 10.00, 'GBP');

CREATE TABLE "erp_connector_demo_v1"."sales_orders" ("company_code" VARCHAR(256), "order_id" VARCHAR(256), "status" VARCHAR(256), "amount" DECIMAL(18,2), "currency" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."sales_orders" VALUES
('1000', 'SO001', 'OPEN', 1200.00, 'INR'),
('1000', 'SO002', 'CANCELED', 400.00, 'INR'),
('2000', 'SO001', 'OPEN', 1000.00, 'USD');

CREATE TABLE "erp_connector_demo_v1"."purchase_orders" ("company_code" VARCHAR(256), "order_id" VARCHAR(256), "status" VARCHAR(256), "amount" DECIMAL(18,2), "currency" VARCHAR(256));

INSERT INTO "erp_connector_demo_v1"."purchase_orders" VALUES
('1000', 'PO001', 'OPEN', 500.00, 'INR'),
('2000', 'PO001', 'OPEN', 700.00, 'USD');
