
-- 
-- Tests for things that can have MySQLaaS compatibility issues
--
-- Notes:
-- Encryption requires this:
--  INSTALL PLUGIN keyring_file SONAME 'keyring_file.so';


--
-- Accounts with restricted privileges
--

DROP USER IF EXISTS testusr1@localhost;
DROP USER IF EXISTS testusr2@localhost;
DROP USER IF EXISTS testusr3@localhost;
DROP USER IF EXISTS testusr4@localhost;
DROP USER IF EXISTS testusr5@localhost;
DROP USER IF EXISTS testusr6@localhost;

-- only restricted privs

CREATE USER testusr1@localhost;
GRANT SUPER, FILE, RELOAD/*!80000 , BINLOG_ADMIN */ ON *.* TO testusr1@localhost;

CREATE USER testusr2@localhost;
GRANT SUPER ON *.* TO testusr2@localhost;

CREATE USER testusr3@localhost;
GRANT FILE, RELOAD/*!80000 , BINLOG_ADMIN */ ON *.* TO testusr3@localhost WITH GRANT OPTION;

-- mixed privs
CREATE USER testusr4@localhost;
GRANT SUPER, REPLICATION SLAVE ON *.* TO testusr4@localhost;

CREATE USER testusr5@localhost;
GRANT FILE, REPLICATION SLAVE, SELECT, INSERT, UPDATE, DELETE ON *.* TO testusr5@localhost;

CREATE USER testusr6@localhost;
GRANT FILE ON *.* TO testusr6@localhost;
GRANT SELECT, INSERT, UPDATE, DELETE ON mysqlaas_compat.* TO testusr6@localhost;
GRANT SELECT, INSERT, UPDATE, DELETE ON mysql.* TO testusr6@localhost WITH GRANT OPTION;

--
-- DDL Related
--

DROP SCHEMA IF EXISTS mysqlaas_compat;
CREATE SCHEMA mysqlaas_compat;
USE mysqlaas_compat;

-- Engines

CREATE TABLE myisam_tbl1 (
  id INT PRIMARY KEY auto_increment
) ENGINE=MyISAM CHARSET=latin1;

-- definer clause will be added

CREATE TRIGGER ins_sum BEFORE INSERT ON myisam_tbl1
  FOR EACH ROW SET @sum = @sum + NEW.id;

CREATE TABLE blackhole_tbl1 (
  id INT UNIQUE KEY
) ENGINE=BLACKHOLE CHARSET=latin1;

-- myisam with fulltext index

CREATE TABLE myisam_tbl2 (
  id INT PRIMARY KEY auto_increment,
  text TEXT
) ENGINE=MyISAM CHARSET=utf8mb3;

-- Tablespace dumping not supported in v1

-- TABLESPACE 

CREATE TABLESPACE compat_ts1 ADD DATAFILE 'compat_ts1.ibd' ENGINE=INNODB;

CREATE TABLE ts1_tbl1 (
  pk INT PRIMARY KEY,
  val VARCHAR(40)
) TABLESPACE = compat_ts1 CHARSET=utf8mb4;

-- allowed tablespaces starting with innodb_

CREATE TABLE ts1_tbl2 (
  pk INT PRIMARY KEY,
  val VARCHAR(40)
) TABLESPACE = innodb_system CHARSET=utf8mb4;

/*CREATE TABLE ts1_tbl3 (
  pk INT PRIMARY KEY,
  val VARCHAR(40)
) TABLESPACE = innodb_file_per_table;

-- TABLESPACE with ENCRYPTION is not allowed/restricted (?)

CREATE TABLESPACE ts2 ENCRYPTION='Y';

CREATE TABLESPACE ts3 ENCRYPTION='N';

CREATE TABLE ts2_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts2;

CREATE TABLE ts3_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts3;

-- TABLESPACE with DATAFILE not allowed
CREATE TABLESPACE ts4 ADD DATAFILE '${TMPDIR}/ts4datafile.ibd';

CREATE TABLESPACE ts4_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts4;

-- pathless filename OK (?)
CREATE TABLESPACE ts5 ADD DATAFILE 'ts5datafile.ibd';

CREATE TABLE ts5_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts5;

*/

-- ENCRYPTION is not allowed/restricted (?)

/*CREATE TABLE encr_tbl1 (
  pk INT PRIMARY KEY
) ENCRYPTION = 'Y';

CREATE TABLE encr_tbl2 (
  pk INT PRIMARY KEY
) ENCRYPTION = 'N';*/

-- SQL DEFINER
-- Specifying a DEFINER is not allowed

delimiter //
CREATE procedure labeled()
wholeblock:BEGIN
  DECLARE x INT;
  DECLARE str VARCHAR(255);
  SET x = -5;
  SET str = '';

  loop_label: LOOP
    IF x > 0 THEN
      LEAVE loop_label;
    END IF;
    SET str = CONCAT(str,x,',');
    SET x = x + 1;
    ITERATE loop_label;
  END LOOP;

  SELECT str;

END//
delimiter ;

CREATE DEFINER=root@localhost SQL SECURITY DEFINER VIEW view1 AS
  select 1;

CREATE SQL SECURITY INVOKER VIEW view3 AS
  select 1;

CREATE FUNCTION func1 () RETURNS INT
  NO SQL
  SQL SECURITY DEFINER
  RETURN 0;

CREATE FUNCTION func2 () RETURNS INT
  NO SQL
  SQL SECURITY INVOKER
  RETURN 0;

CREATE PROCEDURE proc1 ()
  NO SQL
  SQL SECURITY DEFINER
BEGIN
END;

CREATE PROCEDURE proc2 ()
  NO SQL
  SQL SECURITY INVOKER
BEGIN
END;

CREATE EVENT event2
  ON SCHEDULE EVERY 1 DAY
DO BEGIN
END;

/*

-- with tablespace

CREATE TABLESPACE pts1;

CREATE TABLE part_tbl10 (
  pk INT PRIMARY KEY
) PARTITION BY HASH(pk)
  TABLESPACE = pts1;

*/

--
-- Other uncommon features without known compat issues
--


-- Partitioning

CREATE TABLE partition_tbl1 (
  pk INT PRIMARY KEY
) PARTITION BY HASH(pk) (
    PARTITION p1 ENGINE=InnoDB
  );

CREATE TABLE partition_tbl2 (
  pk INT PRIMARY KEY
) PARTITION BY HASH(pk) (
    PARTITION p1,
    PARTITION p2
  );

CREATE TABLE partition_tbl3 (
  pk INT PRIMARY KEY
) PARTITION BY RANGE(pk)
  SUBPARTITION BY HASH(pk) (
    PARTITION p1 VALUES LESS THAN (100) (
      SUBPARTITION pk
    )
  );

CREATE TABLE partition_tbl4 (
  pk INT PRIMARY KEY
) PARTITION BY RANGE(pk)
  SUBPARTITION BY LINEAR HASH(pk) (
    PARTITION p1 VALUES LESS THAN (100) (
      SUBPARTITION sp1
    )
  );
