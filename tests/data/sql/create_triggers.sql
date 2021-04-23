SET sql_mode='ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';
DELIMITER //
DROP SCHEMA IF EXISTS test //
CREATE SCHEMA test //
USE test //
CREATE USER test@'%' IDENTIFIED BY 'test' //
GRANT SELECT ON test.* TO test@'%' //
DROP TABLE IF EXISTS test //
CREATE TABLE test(id int NOT NULL AUTO_INCREMENT, col text, PRIMARY KEY (id)) //
DROP TABLE IF EXISTS test2 //
CREATE TABLE test2 (
  id int unsigned NOT NULL AUTO_INCREMENT,
  before_insert int unsigned NOT NULL DEFAULT 0,
  before_update int unsigned NOT NULL DEFAULT 0,
  before_delete int unsigned NOT NULL DEFAULT 0,
  after_insert int unsigned NOT NULL DEFAULT 0,
  after_update int unsigned NOT NULL DEFAULT 0,
  after_delete int unsigned NOT NULL DEFAULT 0,
  after_delete_order char(64) NOT NULL DEFAULT '',
  PRIMARY KEY (id)
) //
DROP TRIGGER IF EXISTS t1 //
CREATE TRIGGER t1 BEFORE INSERT ON test FOR EACH ROW UPDATE test2 SET before_insert = before_insert + 1 //
DROP TRIGGER IF EXISTS t2 //
CREATE TRIGGER t2 BEFORE UPDATE ON test FOR EACH ROW UPDATE test2 SET before_update = before_update + 1 //
DROP TRIGGER IF EXISTS t3 //
CREATE TRIGGER t3 BEFORE DELETE ON test FOR EACH ROW UPDATE test2 SET before_delete = before_delete + 1 //
DROP TRIGGER IF EXISTS t4 //
CREATE TRIGGER t4 AFTER INSERT ON test FOR EACH ROW UPDATE test2 SET after_insert = after_insert + 1 //
DROP TRIGGER IF EXISTS t5 //
CREATE TRIGGER t5 AFTER UPDATE ON test FOR EACH ROW UPDATE test2 SET after_update = after_update + 1 //
DROP TRIGGER IF EXISTS t6 //
CREATE TRIGGER t6 AFTER DELETE ON test FOR EACH ROW BEGIN UPDATE test2 SET after_delete = after_delete + 1; UPDATE test2 SET after_delete_order = RIGHT(CONCAT(after_delete_order, ' t6'), 64); END; //
DROP TRIGGER IF EXISTS t7 //
CREATE TRIGGER t7 AFTER DELETE ON test FOR EACH ROW PRECEDES t6 BEGIN UPDATE test2 SET after_delete = after_delete + 1; UPDATE test2 SET after_delete_order = RIGHT(CONCAT(after_delete_order, ' t7'), 64); END; //
DROP TRIGGER IF EXISTS t8 //
CREATE TRIGGER t8 AFTER DELETE ON test FOR EACH ROW FOLLOWS t7 BEGIN UPDATE test2 SET after_delete = after_delete + 1; UPDATE test2 SET after_delete_order = RIGHT(CONCAT(after_delete_order, " t8"), 64); END; //
SET @old_sql_mode=@@sql_mode //
SELECT @old_sql_mode //
SET sql_mode='ANSI_QUOTES' //
DROP TRIGGER IF EXISTS t9 //
CREATE DEFINER = "test"@"%" TRIGGER t9 AFTER DELETE ON test FOR EACH ROW FOLLOWS t7 BEGIN UPDATE test2 SET after_delete = after_delete + 1; UPDATE test2 SET after_delete_order = RIGHT(CONCAT(after_delete_order, " t9"), 64); END; //
SET @@sql_mode='ANSI_QUOTES,NO_UNSIGNED_SUBTRACTION' //
DROP TRIGGER IF EXISTS t10 //
CREATE TRIGGER t10 AFTER DELETE ON test FOR EACH ROW FOLLOWS t7 BEGIN UPDATE test2 SET after_delete = after_delete + (2 + (CAST(0 AS UNSIGNED) - 1)) ; UPDATE test2 SET after_delete_order = RIGHT(CONCAT(after_delete_order, ' t10'), 64); END; //
SET sql_mode=@old_sql_mode //
SET @old_character_set_client = @@character_set_client //
SET @@character_set_client = 'latin1' //
SET @old_collation_connection = @@collation_connection //
SET @@collation_connection= 'latin1_swedish_ci'  //
DROP TRIGGER IF EXISTS t11 //
CREATE TRIGGER t11 AFTER UPDATE ON test FOR EACH ROW UPDATE test2 SET after_update = after_update + 1  //
SET character_set_client = @old_character_set_client //
SET collation_connection= @old_collation_connection //
DELIMITER ;SQL
