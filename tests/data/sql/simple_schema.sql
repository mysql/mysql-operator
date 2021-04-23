DROP SCHEMA IF EXISTS simple_schema;
CREATE SCHEMA simple_schema;
USE simple_schema;

CREATE TABLE `city` (
  `id` int(11) NOT NULL auto_increment,
  `name` char(35) CHARACTER SET utf8 NOT NULL DEFAULT '',
  `country_code` char(3) CHARACTER SET utf8 NOT NULL DEFAULT '',
  `district` char(20) CHARACTER SET utf8 NOT NULL DEFAULT '',
  `info` json DEFAULT NULL,
   PRIMARY KEY  (`ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;


CREATE VIEW city_list
AS
SELECT c.name AS name, c.country_code AS code
FROM city AS c;


CREATE TABLE `country_info` (
  `doc` json DEFAULT NULL,
  `_id` varchar(32) GENERATED ALWAYS AS (json_unquote(json_extract(doc, '$._id'))) STORED
) ENGINE=InnoDB DEFAULT CHARSET=utf8;




