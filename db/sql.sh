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

# sqlite3 imobile.db -header -column 'select * from users;'
sqlite3 imobile.db < /tmp/tmp.sql
