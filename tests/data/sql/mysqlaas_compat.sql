
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
) ENGINE=MyISAM;

CREATE TABLE blackhole_tbl1 (
  id INT PRIMARY KEY auto_increment
) ENGINE=BLACKHOLE;

-- myisam with fulltext index

CREATE TABLE myisam_tbl2 (
  id INT PRIMARY KEY auto_increment,
  text TEXT
) ENGINE=MyISAM;

-- Tablespace dumping not supported in v1

-- TABLESPACE generally allowed

CREATE TABLESPACE ts1;

CREATE TABLE ts1_tbl1 (
  pk INT PRIMARY KEY,
  val VARCHAR(40)
) TABLESPACE = ts1;

CREATE TABLE ts1_tbl2 (
  pk INT PRIMARY KEY,
  val VARCHAR(40)
) TABLESPACE = innodb_system;

CREATE TABLE ts1_tbl3 (
  pk INT PRIMARY KEY,
  val VARCHAR(40)
) TABLESPACE = innodb_file_per_table;

-- TABLESPACE with ENCRYPTION is not allowed/restricted (?)

CREATE TABLESPACE ts2 ENCRYPTION='Y';

CREATE TABLESPACE ts3 ENCRYPTION='N';

CREATE TABLE ts2_tbl1 (
  pk INT PRIMARY KEY
) ENCRYPTION='Y' TABLESPACE = ts2;

CREATE TABLE ts3_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts3;

-- TABLESPACE with DATAFILE not allowed
CREATE TABLESPACE ts4 ADD DATAFILE '${TMPDIR}/ts4datafile.ibd';

CREATE TABLE ts4_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts4;

-- pathless filename OK (?)
CREATE TABLESPACE ts5 ADD DATAFILE 'ts5datafile.ibd';

CREATE TABLE ts5_tbl1 (
  pk INT PRIMARY KEY
) TABLESPACE = ts5;

-- Tablespace dumping not supported in v1

-- DATA DIRECTORY is not allowed
-- INDEX DIRECTORY is also not allowed and not supported in InnoDB either

CREATE TABLE path_tbl1 (
  pk INT PRIMARY KEY
) DATA DIRECTORY = '${TMPDIR}/test datadir';

CREATE TABLE path_tbl2 (
  pk INT PRIMARY KEY
) ENGINE=MyISAM,
  DATA DIRECTORY = '${TMPDIR}/test datadir',
  INDEX DIRECTORY = '${TMPDIR}/testindexdir';

CREATE TABLE path_tbl3 (
  pk INT PRIMARY KEY
) ENGINE=MyISAM,
  DATA DIRECTORY = '${TMPDIR}/test datadir';

CREATE TABLE path_tbl4 (
  pk INT PRIMARY KEY
) ENGINE=MyISAM,
  INDEX DIRECTORY = '${TMPDIR}/testindexdir';

-- ENCRYPTION is not allowed/restricted (?)

CREATE TABLE encr_tbl1 (
  pk INT PRIMARY KEY
) ENCRYPTION = 'Y';

CREATE TABLE encr_tbl2 (
  pk INT PRIMARY KEY
) ENCRYPTION = 'N';


-- PARTITIONING

CREATE TABLE part_tbl1 (
  pk INT PRIMARY KEY
) PARTITION BY HASH(pk) (
    PARTITION p1
    DATA DIRECTORY = '${TMPDIR}/test datadir'
  );

CREATE TABLE part_tbl2 (
  pk INT PRIMARY KEY
) PARTITION BY HASH(pk) (
    PARTITION p1
    DATA DIRECTORY = '${TMPDIR}/test datadir',
    PARTITION p2
    DATA DIRECTORY = '${TMPDIR}/test datadir2'
  );

CREATE TABLE part_tbl3 (
  pk INT PRIMARY KEY
) PARTITION BY RANGE(pk)
  SUBPARTITION BY HASH(pk) (
    PARTITION p1 VALUES LESS THAN (100)
    DATA DIRECTORY = '${TMPDIR}/test datadir' (
      SUBPARTITION sp1
      DATA DIRECTORY = '${TMPDIR}/test datadir'
    )
  );

-- SQL DEFINER
-- Specifying a DEFINER is not allowed

CREATE DEFINER=root@localhost SQL SECURITY DEFINER VIEW view1 AS
  select 1;

CREATE SQL SECURITY DEFINER VIEW view2 AS
  select 1;

CREATE SQL SECURITY INVOKER VIEW view3 AS
  select 1;

CREATE DEFINER=root@localhost FUNCTION func1 () RETURNS INT
  NO SQL
  SQL SECURITY DEFINER
  RETURN 0;

CREATE FUNCTION func2 () RETURNS INT
  NO SQL
  SQL SECURITY DEFINER
  RETURN 0;

CREATE FUNCTION func3 () RETURNS INT
  NO SQL
  SQL SECURITY INVOKER
  RETURN 0;

CREATE DEFINER=root@localhost PROCEDURE proc1 ()
   NO SQL
   SQL SECURITY DEFINER
BEGIN
END;

CREATE PROCEDURE proc2 ()
  NO SQL
  SQL SECURITY DEFINER
BEGIN
END;

CREATE PROCEDURE proc3 ()
  NO SQL
  SQL SECURITY INVOKER
BEGIN
END;

CREATE DEFINER=root@localhost EVENT event1
  ON SCHEDULE EVERY 1 DAY
DO BEGIN
END;

CREATE EVENT event2
  ON SCHEDULE EVERY 1 DAY
DO BEGIN
END;

-- CREATE DEFINER=root@localhost SQL SECURITY DEFINER EVENT event1


-- with tablespace

/*
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
