#!/bin/bash
#

tee /tmp/tmp.sql <<EOF >/dev/null
/*
.database
.tables
.schema users
.dump
*/
--
.headers on
.mode column
--

SELECT * FROM users;
EOF

#sqlite3 db/imobile.db -header -column 'select * from users;'
#sqlite3 db/imobile.db -line 'select * from users;'

# Recreate db
#rm shared/db/imobile.db; sqlite3 shared/db/imobile.db < shared/db/imobile.sql

#sqlite3 shared/db/imobile.db -line 'select * from app_config'
sqlite3 shared/db/imobile.db 'select * from market_indices; select * from summary_account; select count(*) from holding_stocks;select count(*) from smart_orders; select count(*) from transactions;'

#sqlite3 shared/db/imobile.db -line 'select * from smart_orders limit 1;'
sqlite3 shared/db/imobile.db 'select code,name,valid_until from smart_orders order by valid_until desc;'

#sqlite3 shared/db/imobile.db -line 'select * from holding_stocks limit 1'
#sqlite3 shared/db/imobile.db 'select code,name, transaction_date from transactions order by transaction_date desc;'

