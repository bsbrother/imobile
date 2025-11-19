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
rm db/imobile.db; sqlite3 db/imobile.db < db/imobile.sql

sqlite3 db/imobile.db 'select count(*) from market_indices; select * from summary_account; select count(*) from holding_stocks'
sqlite3 db/imobile.db -line 'select * from holding_stocks limit 1'

