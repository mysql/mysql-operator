drop database if exists mysqldump_test_db;
create database mysqldump_test_db;
use mysqldump_test_db;

# Autoincrement

create table `at1` (
    t1_name varchar(255) default null,
    t1_id int(10) unsigned not null auto_increment,
    key (t1_name),
    primary key (t1_id)
) auto_increment = 1000 default charset=latin1;

insert into at1 (t1_name) values('bla');
insert into at1 (t1_name) values('bla');
insert into at1 (t1_name) values('bla');

# --exec $MYSQL_DUMP --skip-comments test t1 > $MYSQLTEST_VARDIR/tmp/bug19025.sql

# Views

CREATE TABLE tv2 (
  a varchar(30) default NULL,
  KEY a (a(5))
);

INSERT INTO tv2 VALUES ('alfred');
INSERT INTO tv2 VALUES ('angie');
INSERT INTO tv2 VALUES ('bingo');
INSERT INTO tv2 VALUES ('waffle');
INSERT INTO tv2 VALUES ('lemon');
create view v2 as select * from tv2 where a like 'a%' with check option;

create table tv1(a int, b int, c varchar(30));

insert into tv1 values(1, 2, "one"), (2, 4, "two"), (3, 6, "three");

create view v3 as
select * from tv1;

create  view v1 as
select * from v3 where b in (1, 2, 3, 4, 5, 6, 7);

create  view v4 as
select v3.a from v3, v1 where v1.a=v3.a and v3.b=3 limit 1;

# --exec $MYSQL_DUMP --skip-comments test

# Test for dumping triggers

CREATE TABLE t1 (a int, b bigint default NULL);
CREATE TABLE t2 (a int);
delimiter |
create trigger trg1 before insert on t1 for each row
begin
  if new.a > 10 then
    set new.a := 10;
    set new.a := 11;
  end if;
end|
create trigger trg2 before update on t1 for each row begin
  if old.a % 2 = 0 then set new.b := 12; end if;
end|
set sql_mode="traditional"|
create trigger trg3 after update on t1 for each row
begin
  if new.a = -1 then
    set @fired:= "Yes";
  end if;
end|
create trigger trg4 before insert on t2 for each row
begin
  if new.a > 10 then
    set @fired:= "No";
  end if;
end|
set sql_mode=default|
delimiter ;

INSERT INTO t1 (a) VALUES (1),(2),(3),(22);
update t1 set a = 4 where a=3;
# Triggers should be dumped by default
# --exec $MYSQL_DUMP --skip-comments --databases test
# Skip dumping triggers
# --exec $MYSQL_DUMP --skip-comments --databases --skip-triggers test
# Dump and reload...
# --exec $MYSQL_DUMP --skip-comments --databases test > $MYSQLTEST_VARDIR/tmp/mysqldump.sql

# --exec $MYSQL test < $MYSQLTEST_VARDIR/tmp/mysqldump.sql


# Events

create event ee1 on schedule at '2035-12-31 20:01:23' do set @a=5;    	 	
create event ee2 on schedule at '2029-12-31 21:01:23' do set @a=5;
# --exec $MYSQL_DUMP --events second > $MYSQLTEST_VARDIR/tmp/bug16853-2.sql


# Routines

SET GLOBAL log_bin_trust_function_creators = 1;

CREATE TABLE tr1 (id int);
INSERT INTO tr1 VALUES(1), (2), (3), (4), (5);

DELIMITER //
CREATE FUNCTION `bug9056_func1`(a INT, b INT) RETURNS int(11) RETURN a+b //
CREATE PROCEDURE `bug9056_proc1`(IN a INT, IN b INT, OUT c INT)
BEGIN SELECT a+b INTO c; end  //

create function bug9056_func2(f1 char binary) returns char
begin
  set f1= concat( 'hello', f1 );
  return f1;
end //

CREATE PROCEDURE bug9056_proc2(OUT a INT)
BEGIN
  select sum(id) from tr1 into a;
END //

DELIMITER ;

set sql_mode='ansi';
create procedure `a'b` () select 1; # to fix syntax highlighting :')
set sql_mode='';

# Dump the DB and ROUTINES
# --exec $MYSQL_DUMP --skip-comments --routines --databases test

