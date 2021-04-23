
SET foreign_key_checks=0;

DROP SCHEMA IF EXISTS all_features;
DROP SCHEMA IF EXISTS all_features2;

CREATE SCHEMA all_features;
USE all_features;


-- Users

/* TODO 
CREATE USER `new
line`@localhost;
CREATE USER `'foo'@bar'`@localhost;
CREATE USER `foo``bar`@localhost;
CREATE USER `foo``bar`@'''foo.com';
CREATE USER ''''@'''';
CREATE USER ''@localhost;
*/

-- Tables

/* TODO
CREATE TABLE `new
line` (`a
column`  INT PRIMARY KEY);

CREATE TABLE `foo``bar(` (
    `col``umn` INT PRIMARY KEY
);

CREATE TABLE ```` (
    ```` INT PRIMARY KEY
);

CREATE TABLE `'"` (
    `'"` INT PRIMARY KEY
);

CREATE TABLE table1 (
    a INT,
    b INT,
    PRIMARY KEY (a,b)
);
INSERT INTO table1 VALUES (1,1), (1,2), (2,1), (3,1), (3,2), (3,3), (2,2), (2,3);

CREATE TABLE table2 (
    a VARCHAR(10),
    b VARCHAR(10),
    PRIMARY KEY (a,b)
);
INSERT INTO table2 VALUES ('one','one'), ('one','two'), ('two','one'), ('three','one'), ('three','two'), ('three','three'), ('two','two'), ('two','three');
*/

/* test case for when the table name is included in a comment */
CREATE TABLE `*/` (
  a int primary key
);

-- Functional Indexes

/*!50700 CREATE TABLE `findextable` (
  `data` json DEFAULT NULL */
/*!80000 , UNIQUE KEY `findextable_idx` ((cast(json_extract(`data`,_utf8mb4'$._id') as unsigned array))) */
/*!50700 ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 */;

/*!50700 CREATE TABLE `findextable2` (
  pk int primary key,
  `data` json DEFAULT NULL */
/*!80000 , UNIQUE KEY `findextable_idx` ((cast(json_extract(`data`,_utf8mb4'$._id') as unsigned array))) */
/*!50700 ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 */;

/*!50700 CREATE TABLE `findextable3` (
  `data` json DEFAULT NULL,
  num int default 42 */ /*!80000 ,
  ts int default ((now())),
  KEY `findextable_idx` ((num+1)) */
/*!50700 ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 */;

-- Generated Columns

/*!50700 CREATE TABLE gctable1 (
    a INT PRIMARY KEY,
    b INT GENERATED ALWAYS AS (sqrt(a)) VIRTUAL
) */; 

-- Constraints

CREATE TABLE `aik` (
  `id` int NOT NULL,
  `uai` int NOT NULL AUTO_INCREMENT,
  `data` text,
  PRIMARY KEY (`id`),
  KEY `uai` (`uai`)
);
insert into aik values (1, 0, "text");

-- Foreign Keys

-- Tables and views with circular FK references
-- This is to ensure that the schema can be loaded without FK dependency
-- related errors.

CREATE SCHEMA all_features2;


CREATE TABLE all_features.tbl1 (
  a int primary key,
  b int,
  CONSTRAINT FOREIGN KEY (b) REFERENCES all_features2.tbl2(a)
);

CREATE TABLE all_features.tbl2 (
  a int primary key,
  b int,
  CONSTRAINT FOREIGN KEY (b) REFERENCES all_features.tbl1(a)
);

CREATE TABLE all_features.v1 (
  a int primary key
);

CREATE TABLE all_features.v2 (
  a int primary key
);

CREATE TABLE all_features2.tbl1 (
  a int primary key,
  b int,
  CONSTRAINT FOREIGN KEY (b) REFERENCES all_features.tbl1(a)
);

CREATE TABLE all_features2.tbl2 (
  a int primary key,
  b int,
  CONSTRAINT FOREIGN KEY (b) REFERENCES all_features2.tbl1(a)
);

CREATE TABLE all_features2.v1 (
  a int primary key
);

CREATE TABLE all_features2.v2 (
  a int primary key
);


DROP TABLE IF EXISTS all_features.v1;
CREATE VIEW all_features.v1 AS SELECT * FROM all_features.v2;

DROP TABLE IF EXISTS all_features.v2;
CREATE VIEW all_features.v2 AS SELECT * FROM all_features2.v1;

DROP TABLE IF EXISTS all_features2.v1;
CREATE VIEW all_features2.v1 AS SELECT * FROM all_features2.v2;

DROP TABLE IF EXISTS all_features2.v2;
CREATE VIEW all_features2.v2 AS SELECT * FROM all_features.tbl2;


-- Attributes (auto_increment)

CREATE TABLE all_features.plaintable (
  a int primary key auto_increment,
  b varchar(200)
);

-- Partitions

-- Triggers

CREATE TRIGGER all_features.mytrigger BEFORE INSERT ON all_features.plaintable FOR EACH ROW BEGIN END;

/* TODO
CREATE TRIGGER `new
line` DO SELECT 1;

CREATE TRIGGER `foo``bar(` DO SELECT 1;

CREATE TRIGGER ```` DO SELECT 1;

CREATE TRIGGER `'"` DO SELECT 1;
*/

-- Views

/* TODO
CREATE VIEW `new
line` AS SELECT 1;

CREATE VIEW `foo``bar(` AS SELECT 1;

CREATE VIEW ```` AS SELECT 1;

CREATE VIEW `'"` AS SELECT 1;
*/

-- Procedures

/* TODO
CREATE PROCEDURE `new
line`() BEGIN END;

CREATE PROCEDURE `foo``bar(`() BEGIN END;

CREATE PROCEDURE ````() BEGIN END;

CREATE PROCEDURE `'"`() BEGIN END;
*/

-- Functions

/* TODO
CREATE FUNCTION `new
line`() RETURNS INT RETURN 0;

CREATE FUNCTION `foo``bar(`() RETURNS INT RETURN 0;

CREATE FUNCTION ````() RETURNS INT RETURN 0;

CREATE FUNCTION `'"`() RETURNS INT RETURN 0;
*/

-- Events

CREATE EVENT all_features.myevent ON SCHEDULE AT '2000-01-01 00:00:00' ON COMPLETION PRESERVE DO begin end;

/* TODO
CREATE EVENT `new
line`() BEGIN END;

CREATE EVENT `foo``bar(`() BEGIN END;

CREATE EVENT ````() BEGIN END;

CREATE EVENT `'"`() BEGIN END;
*/

-- Charsets

USE all_features;

CREATE TABLE latin1_charset (
    id int primary key auto_increment,
    latin varchar(200)
) CHARACTER SET latin1;

INSERT INTO latin1_charset VALUES (DEFAULT, unhex('4a61636172e9'));
INSERT INTO latin1_charset VALUES (DEFAULT, unhex('44656c66ed6e'));
INSERT INTO latin1_charset VALUES (DEFAULT, 'Lagarto');
INSERT INTO latin1_charset VALUES (DEFAULT, unhex('4d61e7e3'));

CREATE TABLE sjis_charset (
    id int primary key auto_increment,
    japanese varchar(200) 
) CHARACTER SET sjis;

INSERT INTO sjis_charset VALUES (DEFAULT, unhex('82ed82c9'));
INSERT INTO sjis_charset VALUES (DEFAULT, unhex('8343838b834a'));
INSERT INTO sjis_charset VALUES (DEFAULT, unhex('e592e58e'));
INSERT INTO sjis_charset VALUES (DEFAULT, unhex('838a83938353'));

CREATE TABLE unicode_charset (
    id int primary key auto_increment,
    anything varchar(200)
) CHARACTER SET utf8mb4;

INSERT INTO unicode_charset VALUES (DEFAULT, _utf8mb4'🐊');
INSERT INTO unicode_charset VALUES (DEFAULT, _utf8mb4'🐬');
INSERT INTO unicode_charset VALUES (DEFAULT, _utf8mb4'🦎');
INSERT INTO unicode_charset VALUES (DEFAULT, _utf8mb4'🍎');


CREATE TABLE mixed_charset (
    id int primary key auto_increment,
    latin varchar(200) CHARACTER SET latin1,
    japanese varchar(200) CHARACTER SET sjis,
    anything varchar(200) CHARACTER SET utf8mb4,
    picture blob
);

INSERT INTO mixed_charset VALUES (DEFAULT, unhex('4a61636172e9'), unhex('82ed82c9'), _utf8mb4'🐊', unhex('7397553f03b849167ec78ab87eed8063'));
INSERT INTO mixed_charset VALUES (DEFAULT, unhex('44656c66ed6e'), unhex('8343838b834a'), _utf8mb4'🐬', unhex('36cdf8b887a5cffc78dcd5c08991b993'));
INSERT INTO mixed_charset VALUES (DEFAULT, 'Lagarto', unhex('e592e58e'), _utf8mb4'🦎', unhex('5046a43fd3f8184be864359e3d5c9bda'));
INSERT INTO mixed_charset VALUES (DEFAULT, unhex('4d61e7e3'), unhex('838a83938353'), _utf8mb4'🍎', unhex('1f3870be274f6c49b3e31a0c6728957f'));
