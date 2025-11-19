PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;

CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL,
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

CREATE TABLE users (
	id INTEGER NOT NULL,
	email VARCHAR NOT NULL,
	password_hash VARCHAR NOT NULL,
	is_verified BOOLEAN,
	first_name VARCHAR,
	last_name VARCHAR,
	phone_number VARCHAR,
	profile_picture_url VARCHAR,
	language VARCHAR,
	PRIMARY KEY (id),
	UNIQUE (email)
);
CREATE INDEX ix_users_id ON users (id);
CREATE UNIQUE INDEX ix_users_email ON users (email);
INSERT INTO users VALUES(1,'demo@example.com','$2b$12$cYKRWkdkf56QDijzeGOOn.LNWWzLCIVahgGpHlpcb8MpBv9sSCz9C',1,'Demo','User',NULL,NULL,'en');

-- Create 智能订单 table for storing user smart orders
CREATE TABLE smart_orders (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	code VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	trigger_condition VARCHAR NOT NULL, -- e.g., 'price_above', 'price_below'
	buy_or_sell_price_type VARCHAR NOT NULL, -- e.g., 'market_price', 'limit_price'
	buy_or_sell_quantity INTEGER NOT NULL,
	valid_until VARCHAR DEFAULT '',  -- e.g., 'end_of_day', 'good_till_cancelled'
	order_number VARCHAR UNIQUE,
	reason_of_ending VARCHAR,
	status VARCHAR DEFAULT 'cancelled', -- 'running', 'paused', 'completed', 'cancelled'
	last_updated DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_smart_orders_id ON smart_orders (id);
CREATE INDEX ix_smart_orders_user_id ON smart_orders (user_id);
CREATE INDEX ix_smart_orders_code ON smart_orders (code);
CREATE INDEX ix_smart_orders_name ON smart_orders (name);
CREATE INDEX ix_smart_orders_status ON smart_orders (status);
CREATE INDEX ix_smart_orders_last_updated ON smart_orders (last_updated);
CREATE UNIQUE INDEX ix_smart_orders_order_number ON smart_orders (order_number);

-- Create strategy table for storing user strategies
CREATE TABLE strategies (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	strategy_name VARCHAR NOT NULL,
	description VARCHAR,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (id),
	UNIQUE (user_id, strategy_name),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_strategies_id ON strategies (id);
CREATE UNIQUE INDEX ix_strategies_user_strategy ON strategies (user_id, strategy_name);

-- Create configuration table for storing app settings
CREATE TABLE app_config (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	theme VARCHAR DEFAULT 'light', -- 'light' or 'dark'
	notifications_enabled BOOLEAN DEFAULT 1,
	market VARCHAR DEFAULT 'A-shares', -- 'A-shares', 'US', 'HK'
	default_currency VARCHAR DEFAULT 'CNY', -- 'CNY' or 'USD'
	language VARCHAR DEFAULT 'en',
	data_refresh_interval INTEGER DEFAULT 15, -- in minutes
	last_synced DATETIME,
	open_time_morning_start TIME DEFAULT '09:30:00',
	open_time_morning_end TIME DEFAULT '11:30:00',
	open_time_afternoon_start TIME DEFAULT '13:00:00',
	open_time_afternoon_end TIME DEFAULT '15:00:00',
	PRIMARY KEY (id),
	UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_app_config_id ON app_config (id);
CREATE UNIQUE INDEX ix_app_config_user_id ON app_config (user_id);
INSERT INTO app_config (id, user_id) VALUES(1,1);

CREATE TABLE summary_account (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	total_market_value FLOAT,
	today_pnl FLOAT,
	today_pnl_percent FLOAT,
	cumulative_pnl FLOAT,
	cumulative_pnl_percent FLOAT,
	cash FLOAT,
	floating_pnl_summary FLOAT,
	floating_pnl_summary_percent FLOAT,
	total_assets FLOAT,
	principal FLOAT,
	position_percent FLOAT,
	withdrawable FLOAT,
	last_updated DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_summary_account_id ON summary_account (id);
CREATE UNIQUE INDEX ix_summary_account_user_id ON summary_account (user_id);
CREATE INDEX ix_summary_account_last_updated ON summary_account (last_updated);
INSERT INTO summary_account VALUES(1,1,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,300000.0,0.0,0.0,'2024-06-20 00:00:00');

-- Holding stocks table for user's current stock holdings
CREATE TABLE holding_stocks (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	code VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	current_price FLOAT,
	change FLOAT,
	change_percent FLOAT,
	market_value FLOAT,
	holdings INTEGER,
	cost_basis_diluted FLOAT,
	cost_basis_total FLOAT,
	pnl_float FLOAT,
	pnl_float_percent FLOAT,
	pnl_cumulative FLOAT,
	pnl_cumulative_percent FLOAT,
	available_shares INTEGER,
	last_updated DATETIME NOT NULL,
	analysis_report_url VARCHAR,
	operation_cmd_url VARCHAR,
	PRIMARY KEY (id),
	UNIQUE (user_id, code),
	UNIQUE (user_id, name),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_holding_stocks_id ON holding_stocks (id);
CREATE INDEX ix_holding_stocks_code ON holding_stocks (code);
CREATE INDEX ix_holding_stocks_name ON holding_stocks (name);
CREATE UNIQUE INDEX ix_holding_stocks_userid_code ON holding_stocks (user_id, code);
CREATE UNIQUE INDEX ix_holding_stocks_userid_name ON holding_stocks (user_id, name);
CREATE INDEX ix_holding_stocks_last_updated ON holding_stocks (last_updated);

-- Portfolio performance history table for tracking daily/historical performance
CREATE TABLE portfolio_history (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	record_date DATE NOT NULL,
	total_assets FLOAT,
	total_market_value FLOAT,
	cash FLOAT,
	daily_pnl FLOAT,
	daily_pnl_percent FLOAT,
	cumulative_pnl FLOAT,
	cumulative_pnl_percent FLOAT,
	PRIMARY KEY (id),
	UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_portfolio_history_id ON portfolio_history (id);
CREATE INDEX ix_portfolio_history_user_date ON portfolio_history (user_id, record_date);

-- Transaction history table for buy/sell records
CREATE TABLE transactions (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	code VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	transaction_type VARCHAR NOT NULL, -- 'buy' or 'sell'
	transaction_date DATETIME NOT NULL,
	price FLOAT NOT NULL,
	quantity INTEGER NOT NULL,
	amount FLOAT NOT NULL,
	commission FLOAT,
	tax FLOAT,
	net_amount FLOAT,
	notes VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_transactions_id ON transactions (id);
CREATE INDEX ix_transactions_user_id ON transactions (user_id);
CREATE INDEX ix_transactions_code ON transactions (code);
CREATE INDEX ix_transactions_name ON transactions (name);
CREATE INDEX ix_transactions_date ON transactions (transaction_date);

-- Market indices table for reference benchmarks
CREATE TABLE market_indices (
	id INTEGER NOT NULL,
	index_code VARCHAR NOT NULL,
	index_name VARCHAR NOT NULL,
	current_value FLOAT,
	change FLOAT,
	change_percent FLOAT,
	last_updated DATETIME NOT NULL,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_market_indices_code ON market_indices (index_code);
CREATE INDEX ix_market_indices_id ON market_indices (id);

COMMIT;
