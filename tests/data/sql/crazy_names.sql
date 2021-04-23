
DROP SCHEMA IF EXISTS crazy_names_db;
CREATE SCHEMA crazy_names_db;
USE crazy_names_db;

# crazy names

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

-- Routines

CREATE FUNCTION `new
line`() RETURNS INT RETURN 0;

CREATE FUNCTION `foo``bar(`() RETURNS INT RETURN 0;

CREATE FUNCTION ````() RETURNS INT RETURN 0;

CREATE FUNCTION `'"`() RETURNS INT RETURN 0;


CREATE PROCEDURE `new
line`() BEGIN END;

CREATE PROCEDURE `foo``bar(`() BEGIN END;

CREATE PROCEDURE ````() BEGIN END;

CREATE PROCEDURE `'"`() BEGIN END;

-- Events

CREATE EVENT `new
line` on schedule at '2035-12-31 20:01:23' do set @a=5;

CREATE EVENT `foo``bar(` on schedule at '2035-12-31 20:01:23' do set @a=5;

CREATE EVENT ```` on schedule at '2035-12-31 20:01:23' do set @a=5;

CREATE EVENT `'"` on schedule at '2035-12-31 20:01:23' do set @a=5;

-- Views

CREATE VIEW `new
linev` AS SELECT 1;

CREATE VIEW `foo``barv(` AS SELECT 1;

CREATE VIEW ```v` AS SELECT 1;

CREATE VIEW `'"v` AS SELECT 1;

-- Triggers

CREATE TABLE account (acct_num INT, amount DECIMAL(10,2));

CREATE TRIGGER `new
line` BEFORE INSERT ON account
       FOR EACH ROW SET @sum = @sum + NEW.amount;

CREATE TRIGGER `foo``bar(` BEFORE INSERT ON account
       FOR EACH ROW SET @sum = @sum + NEW.amount;

CREATE TRIGGER ```` BEFORE INSERT ON account
       FOR EACH ROW SET @sum = @sum + NEW.amount;

CREATE TRIGGER `'"` BEFORE INSERT ON account
       FOR EACH ROW SET @sum = @sum + NEW.amount;


