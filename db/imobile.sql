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
INSERT INTO users VALUES(1,'demo@example.com','$2b$12$cYKRWkdkf56QDijzeGOOn.LNWWzLCIVahgGpHlpcb8MpBv9sSCz9C',1,'Demo','User',NULL,NULL,'en');

CREATE TABLE total_table (
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
	last_updated DATETIME,
	PRIMARY KEY (id),
	UNIQUE (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_total_table_last_updated ON total_table (last_updated);

CREATE TABLE stocks_table (
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
	last_updated DATETIME,
	PRIMARY KEY (id),
	UNIQUE (user_id, code),
	UNIQUE (user_id, name),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_users_id ON users (id);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX ix_total_table_id ON total_table (id);
CREATE INDEX ix_stocks_table_id ON stocks_table (id);
CREATE INDEX ix_stocks_table_code ON stocks_table (code);
CREATE INDEX ix_stocks_table_name ON stocks_table (name);
CREATE UNIQUE INDEX ix_stocks_table_userid_code ON stocks_table (user_id, code);
CREATE UNIQUE INDEX ix_stocks_table_userid_name ON stocks_table (user_id, name);
CREATE INDEX ix_stocks_table_last_updated ON stocks_table (last_updated);

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
	stock_code VARCHAR NOT NULL,
	stock_name VARCHAR NOT NULL,
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
CREATE INDEX ix_transactions_stock_code ON transactions (stock_code);
CREATE INDEX ix_transactions_date ON transactions (transaction_date);

-- Market indices table for reference benchmarks
CREATE TABLE market_indices (
	id INTEGER NOT NULL,
	index_code VARCHAR NOT NULL,
	index_name VARCHAR NOT NULL,
	current_value FLOAT,
	change FLOAT,
	change_percent FLOAT,
	last_updated DATETIME,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_market_indices_code ON market_indices (index_code);
CREATE INDEX ix_market_indices_id ON market_indices (id);

-- Stock dividend and split records
CREATE TABLE stock_events (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	stock_code VARCHAR NOT NULL,
	event_type VARCHAR NOT NULL, -- 'dividend', 'split', 'rights_issue'
	event_date DATE NOT NULL,
	description VARCHAR,
	amount FLOAT,
	ratio VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_stock_events_id ON stock_events (id);
CREATE INDEX ix_stock_events_user_stock ON stock_events (user_id, stock_code);

-- Watchlist table for stocks user is tracking but not holding
CREATE TABLE watchlist (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	stock_code VARCHAR NOT NULL,
	stock_name VARCHAR NOT NULL,
	added_date DATETIME NOT NULL,
	notes VARCHAR,
	target_price FLOAT,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_watchlist_id ON watchlist (id);
CREATE INDEX ix_watchlist_user_id ON watchlist (user_id);
CREATE UNIQUE INDEX ix_watchlist_user_stock ON watchlist (user_id, stock_code);

COMMIT;
