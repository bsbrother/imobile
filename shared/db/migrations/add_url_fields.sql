-- Migration: Add URL fields to stocks_table
-- Date: 2025-10-08
-- Description: Add analysis_report_url and operation_cmd_url fields to stocks_table

-- Add analysis_report_url field to store URL to stock analysis report
ALTER TABLE stocks_table ADD COLUMN analysis_report_url VARCHAR;

-- Add operation_cmd_url field to store URL to operation commands
ALTER TABLE stocks_table ADD COLUMN operation_cmd_url VARCHAR;
