SET NAMES 'latin1';
DROP TABLE IF EXISTS client_latin1_table_utf8mb4;
CREATE TABLE client_latin1_table_utf8mb4 (a int auto_increment primary key, txt text ) CHARACTER SET utf8mb4;
INSERT INTO client_latin1_table_utf8mb4 (txt) VALUES ("á"), ("é"), ("ã"), ("ê"), ("💩");
-- The data was inserted with wrong character sets, so the table can only be queried as latin1
-- Querying as utf8mb4 would give garbage
SELECT * FROM client_latin1_table_utf8mb4;
SET NAMES 'utf8mb4';
SELECT * FROM client_latin1_table_utf8mb4;
