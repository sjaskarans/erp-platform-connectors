-- Replace the principal placeholder; run as the setup owner.
GRANT USE CATALOG ON CATALOG `erp_demo` TO `<reader-application-id>`;
GRANT USE SCHEMA ON SCHEMA `erp_demo`.`erp_connector_demo_v1` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`companies` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`customers` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`suppliers` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`finance_lines` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`receivables` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`payables` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`inventory_positions` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`sales_orders` TO `<reader-application-id>`;
GRANT SELECT ON TABLE `erp_demo`.`erp_connector_demo_v1`.`purchase_orders` TO `<reader-application-id>`;
-- Also grant CAN USE on the SQL warehouse through its permissions UI.
