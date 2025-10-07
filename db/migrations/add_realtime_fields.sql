-- Migration script to add fields for real-time data from mobile app
-- Date: 2025-10-06

-- Add position_percent and withdrawable to total_table
ALTER TABLE total_table ADD COLUMN position_percent FLOAT;
ALTER TABLE total_table ADD COLUMN withdrawable FLOAT;

-- Add available_shares to stocks_table  
ALTER TABLE stocks_table ADD COLUMN available_shares INTEGER;

-- Add last_updated timestamp to stocks_table for tracking data freshness
ALTER TABLE stocks_table ADD COLUMN last_updated DATETIME;

-- Add last_updated to total_table for tracking data freshness
ALTER TABLE total_table ADD COLUMN last_updated DATETIME;

-- Create index on last_updated for performance
CREATE INDEX ix_stocks_table_last_updated ON stocks_table (last_updated);
CREATE INDEX ix_total_table_last_updated ON total_table (last_updated);
