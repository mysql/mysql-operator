-- MySQL dump 10.13  Distrib 8.0.17, for macos10.14 (x86_64)
--
-- Host: 0    Database: mysql_innodb_cluster_metadata
-- ------------------------------------------------------
-- Server version	8.0.17-commercial

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- GTID state at the beginning of the backup 
--

--
-- Table structure for table `async_cluster_members`
--

DROP TABLE IF EXISTS `async_cluster_members`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `async_cluster_members` (
  `cluster_id` char(36) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `view_id` int(10) unsigned NOT NULL,
  `instance_id` int(10) unsigned NOT NULL,
  `master_instance_id` int(10) unsigned DEFAULT NULL,
  `primary_master` tinyint(1) NOT NULL,
  `attributes` json NOT NULL,
  PRIMARY KEY (`cluster_id`,`view_id`,`instance_id`),
  KEY `view_id` (`view_id`),
  KEY `instance_id` (`instance_id`),
  CONSTRAINT `async_cluster_members_ibfk_1` FOREIGN KEY (`cluster_id`, `view_id`) REFERENCES `async_cluster_views` (`cluster_id`, `view_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `async_cluster_members`
--

LOCK TABLES `async_cluster_members` WRITE;
/*!40000 ALTER TABLE `async_cluster_members` DISABLE KEYS */;
INSERT INTO `async_cluster_members` VALUES ('e180bd80-e0b1-11e9-a344-14dc13c8b225',2,1,NULL,1,'{\"instance.address\": \"127.0.0.1:3000\", \"instance.mysql_server_uuid\": \"bf17150a-e0b1-11e9-bd3e-923d79a7db52\"}');
/*!40000 ALTER TABLE `async_cluster_members` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `async_cluster_views`
--

DROP TABLE IF EXISTS `async_cluster_views`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `async_cluster_views` (
  `cluster_id` char(36) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `view_id` int(10) unsigned NOT NULL,
  `topology_type` enum('SINGLE-PRIMARY-TREE') DEFAULT NULL,
  `view_change_reason` varchar(32) NOT NULL,
  `view_change_time` timestamp(6) NOT NULL,
  `view_change_info` json NOT NULL,
  `attributes` json NOT NULL,
  PRIMARY KEY (`cluster_id`,`view_id`),
  KEY `view_id` (`view_id`),
  CONSTRAINT `async_cluster_views_ibfk_1` FOREIGN KEY (`cluster_id`) REFERENCES `clusters` (`cluster_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `async_cluster_views`
--

LOCK TABLES `async_cluster_views` WRITE;
/*!40000 ALTER TABLE `async_cluster_views` DISABLE KEYS */;
INSERT INTO `async_cluster_views` VALUES ('e180bd80-e0b1-11e9-a344-14dc13c8b225',1,'SINGLE-PRIMARY-TREE','CREATE','2019-09-26 23:03:40.865766','{\"user\": \"root@localhost\", \"source\": \"bf17150a-e0b1-11e9-bd3e-923d79a7db52\"}','{}'),('e180bd80-e0b1-11e9-a344-14dc13c8b225',2,'SINGLE-PRIMARY-TREE','ADD_INSTANCE','2019-09-26 23:03:40.874964','{\"user\": \"root@localhost\", \"source\": \"bf17150a-e0b1-11e9-bd3e-923d79a7db52\"}','{}');
/*!40000 ALTER TABLE `async_cluster_views` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `clusters`
--

DROP TABLE IF EXISTS `clusters`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `clusters` (
  `cluster_id` char(36) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `cluster_name` varchar(40) NOT NULL,
  `description` text,
  `options` json DEFAULT NULL,
  `attributes` json DEFAULT NULL,
  `cluster_type` enum('gr','ar') NOT NULL,
  `primary_mode` enum('pm','mm') NOT NULL DEFAULT 'pm',
  `router_options` json DEFAULT NULL,
  PRIMARY KEY (`cluster_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `clusters`
--

LOCK TABLES `clusters` WRITE;
/*!40000 ALTER TABLE `clusters` DISABLE KEYS */;
INSERT INTO `clusters` VALUES ('e180bd80-e0b1-11e9-a344-14dc13c8b225','myrs','Default Replica Set',NULL,'{\"adopted\": 0, \"opt_gtidSetIsComplete\": false}','ar','pm',NULL);
/*!40000 ALTER TABLE `clusters` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `instances`
--

DROP TABLE IF EXISTS `instances`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `instances` (
  `instance_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `cluster_id` char(36) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `address` varchar(256) CHARACTER SET ascii COLLATE ascii_general_ci DEFAULT NULL,
  `mysql_server_uuid` char(36) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `instance_name` varchar(265) NOT NULL,
  `addresses` json NOT NULL,
  `attributes` json DEFAULT NULL,
  `description` text,
  PRIMARY KEY (`instance_id`,`cluster_id`),
  KEY `cluster_id` (`cluster_id`),
  CONSTRAINT `instances_ibfk_1` FOREIGN KEY (`cluster_id`) REFERENCES `clusters` (`cluster_id`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `instances`
--

LOCK TABLES `instances` WRITE;
/*!40000 ALTER TABLE `instances` DISABLE KEYS */;
INSERT INTO `instances` VALUES (1,'e180bd80-e0b1-11e9-a344-14dc13c8b225','127.0.0.1:3000','11111111-1111-1111-1111-111111111111','127.0.0.1:3000','{\"mysqlX\": \"127.0.0.1:30000\", \"mysqlClassic\": \"127.0.0.1:3000\"}','{}',NULL);
/*!40000 ALTER TABLE `instances` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `router_rest_accounts`
--

DROP TABLE IF EXISTS `router_rest_accounts`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `router_rest_accounts` (
  `cluster_id` char(36) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `user` varchar(256) NOT NULL,
  `authentication_method` varchar(64) NOT NULL DEFAULT 'modular_crypt_format',
  `authentication_string` text CHARACTER SET ascii COLLATE ascii_general_ci,
  `description` varchar(255) DEFAULT NULL,
  `privileges` json DEFAULT NULL,
  `attributes` json DEFAULT NULL,
  PRIMARY KEY (`cluster_id`,`user`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `router_rest_accounts`
--

LOCK TABLES `router_rest_accounts` WRITE;
/*!40000 ALTER TABLE `router_rest_accounts` DISABLE KEYS */;
/*!40000 ALTER TABLE `router_rest_accounts` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `routers`
--

DROP TABLE IF EXISTS `routers`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `routers` (
  `router_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `router_name` varchar(265) NOT NULL,
  `product_name` varchar(128) NOT NULL,
  `address` varchar(256) CHARACTER SET ascii COLLATE ascii_general_ci DEFAULT NULL,
  `version` varchar(12) DEFAULT NULL,
  `last_check_in` timestamp NULL DEFAULT NULL,
  `attributes` json DEFAULT NULL,
  `cluster_id` char(36) CHARACTER SET ascii COLLATE ascii_general_ci DEFAULT NULL,
  `options` json DEFAULT NULL,
  PRIMARY KEY (`router_id`),
  UNIQUE KEY `address` (`address`,`router_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `routers`
--

LOCK TABLES `routers` WRITE;
/*!40000 ALTER TABLE `routers` DISABLE KEYS */;
/*!40000 ALTER TABLE `routers` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Temporary view structure for view `schema_version`
--

DROP TABLE IF EXISTS `schema_version`;
/*!50001 DROP VIEW IF EXISTS `schema_version`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `schema_version` AS SELECT 
 1 AS `major`,
 1 AS `minor`,
 1 AS `patch`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_ar_clusters`
--

DROP TABLE IF EXISTS `v2_ar_clusters`;
/*!50001 DROP VIEW IF EXISTS `v2_ar_clusters`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_ar_clusters` AS SELECT 
 1 AS `view_id`,
 1 AS `cluster_type`,
 1 AS `primary_mode`,
 1 AS `async_topology_type`,
 1 AS `cluster_id`,
 1 AS `cluster_name`,
 1 AS `attributes`,
 1 AS `options`,
 1 AS `router_options`,
 1 AS `description`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_ar_members`
--

DROP TABLE IF EXISTS `v2_ar_members`;
/*!50001 DROP VIEW IF EXISTS `v2_ar_members`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_ar_members` AS SELECT 
 1 AS `view_id`,
 1 AS `cluster_id`,
 1 AS `instance_id`,
 1 AS `label`,
 1 AS `member_id`,
 1 AS `member_role`,
 1 AS `master_instance_id`,
 1 AS `master_member_id`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_clusters`
--

DROP TABLE IF EXISTS `v2_clusters`;
/*!50001 DROP VIEW IF EXISTS `v2_clusters`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_clusters` AS SELECT 
 1 AS `cluster_type`,
 1 AS `primary_mode`,
 1 AS `cluster_id`,
 1 AS `cluster_name`,
 1 AS `router_options`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_gr_clusters`
--

DROP TABLE IF EXISTS `v2_gr_clusters`;
/*!50001 DROP VIEW IF EXISTS `v2_gr_clusters`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_gr_clusters` AS SELECT 
 1 AS `cluster_type`,
 1 AS `primary_mode`,
 1 AS `cluster_id`,
 1 AS `cluster_name`,
 1 AS `group_name`,
 1 AS `attributes`,
 1 AS `options`,
 1 AS `router_options`,
 1 AS `description`,
 1 AS `replicated_cluster_id`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_instances`
--

DROP TABLE IF EXISTS `v2_instances`;
/*!50001 DROP VIEW IF EXISTS `v2_instances`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_instances` AS SELECT 
 1 AS `instance_id`,
 1 AS `cluster_id`,
 1 AS `label`,
 1 AS `mysql_server_uuid`,
 1 AS `address`,
 1 AS `endpoint`,
 1 AS `xendpoint`,
 1 AS `attributes`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_router_rest_accounts`
--

DROP TABLE IF EXISTS `v2_router_rest_accounts`;
/*!50001 DROP VIEW IF EXISTS `v2_router_rest_accounts`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_router_rest_accounts` AS SELECT 
 1 AS `cluster_id`,
 1 AS `user`,
 1 AS `authentication_method`,
 1 AS `authentication_string`,
 1 AS `description`,
 1 AS `privileges`,
 1 AS `attributes`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_routers`
--

DROP TABLE IF EXISTS `v2_routers`;
/*!50001 DROP VIEW IF EXISTS `v2_routers`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_routers` AS SELECT 
 1 AS `router_id`,
 1 AS `cluster_id`,
 1 AS `router_name`,
 1 AS `product_name`,
 1 AS `address`,
 1 AS `version`,
 1 AS `last_check_in`,
 1 AS `attributes`,
 1 AS `options`*/;
SET character_set_client = @saved_cs_client;

--
-- Temporary view structure for view `v2_this_instance`
--

DROP TABLE IF EXISTS `v2_this_instance`;
/*!50001 DROP VIEW IF EXISTS `v2_this_instance`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `v2_this_instance` AS SELECT 
 1 AS `cluster_id`,
 1 AS `instance_id`,
 1 AS `cluster_name`,
 1 AS `cluster_type`*/;
SET character_set_client = @saved_cs_client;

--
-- Final view structure for view `schema_version`
--

/*!50001 DROP VIEW IF EXISTS `schema_version`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `schema_version` (`major`,`minor`,`patch`) AS select 2 AS `2`,0 AS `0`,0 AS `0` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_ar_clusters`
--

/*!50001 DROP VIEW IF EXISTS `v2_ar_clusters`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_ar_clusters` AS select `acv`.`view_id` AS `view_id`,`c`.`cluster_type` AS `cluster_type`,`c`.`primary_mode` AS `primary_mode`,`acv`.`topology_type` AS `async_topology_type`,`c`.`cluster_id` AS `cluster_id`,`c`.`cluster_name` AS `cluster_name`,`c`.`attributes` AS `attributes`,`c`.`options` AS `options`,`c`.`router_options` AS `router_options`,`c`.`description` AS `description` from (`clusters` `c` join `async_cluster_views` `acv` on((`c`.`cluster_id` = `acv`.`cluster_id`))) where ((`acv`.`view_id` = (select max(`async_cluster_views`.`view_id`) from `async_cluster_views`)) and (`c`.`cluster_type` = 'ar')) */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_ar_members`
--

/*!50001 DROP VIEW IF EXISTS `v2_ar_members`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_ar_members` AS select `acm`.`view_id` AS `view_id`,`i`.`cluster_id` AS `cluster_id`,`i`.`instance_id` AS `instance_id`,`i`.`instance_name` AS `label`,`i`.`mysql_server_uuid` AS `member_id`,if(`acm`.`primary_master`,'PRIMARY','SECONDARY') AS `member_role`,`acm`.`master_instance_id` AS `master_instance_id`,`mi`.`mysql_server_uuid` AS `master_member_id` from ((`instances` `i` left join `async_cluster_members` `acm` on(((`acm`.`cluster_id` = `i`.`cluster_id`) and (`acm`.`instance_id` = `i`.`instance_id`)))) left join `instances` `mi` on((`mi`.`instance_id` = `acm`.`master_instance_id`))) where (`acm`.`view_id` = (select max(`async_cluster_views`.`view_id`) from `async_cluster_views`)) */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_clusters`
--

/*!50001 DROP VIEW IF EXISTS `v2_clusters`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_clusters` AS select `c`.`cluster_type` AS `cluster_type`,`c`.`primary_mode` AS `primary_mode`,`c`.`cluster_id` AS `cluster_id`,`c`.`cluster_name` AS `cluster_name`,`c`.`router_options` AS `router_options` from `clusters` `c` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_gr_clusters`
--

/*!50001 DROP VIEW IF EXISTS `v2_gr_clusters`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_gr_clusters` AS select `c`.`cluster_type` AS `cluster_type`,`c`.`primary_mode` AS `primary_mode`,`c`.`cluster_id` AS `cluster_id`,`c`.`cluster_name` AS `cluster_name`,json_unquote(json_extract(`c`.`attributes`,'$.group_replication_group_name')) AS `group_name`,`c`.`attributes` AS `attributes`,`c`.`options` AS `options`,`c`.`router_options` AS `router_options`,`c`.`description` AS `description`,NULL AS `replicated_cluster_id` from `clusters` `c` where (`c`.`cluster_type` = 'gr') */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_instances`
--

/*!50001 DROP VIEW IF EXISTS `v2_instances`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_instances` AS select `i`.`instance_id` AS `instance_id`,`i`.`cluster_id` AS `cluster_id`,`i`.`instance_name` AS `label`,`i`.`mysql_server_uuid` AS `mysql_server_uuid`,`i`.`address` AS `address`,json_unquote(json_extract(`i`.`addresses`,'$.mysqlClassic')) AS `endpoint`,json_unquote(json_extract(`i`.`addresses`,'$.mysqlX')) AS `xendpoint`,`i`.`attributes` AS `attributes` from `instances` `i` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_router_rest_accounts`
--

/*!50001 DROP VIEW IF EXISTS `v2_router_rest_accounts`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_router_rest_accounts` AS select `a`.`cluster_id` AS `cluster_id`,`a`.`user` AS `user`,`a`.`authentication_method` AS `authentication_method`,`a`.`authentication_string` AS `authentication_string`,`a`.`description` AS `description`,`a`.`privileges` AS `privileges`,`a`.`attributes` AS `attributes` from `router_rest_accounts` `a` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_routers`
--

/*!50001 DROP VIEW IF EXISTS `v2_routers`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_routers` AS select `r`.`router_id` AS `router_id`,`r`.`cluster_id` AS `cluster_id`,`r`.`router_name` AS `router_name`,`r`.`product_name` AS `product_name`,`r`.`address` AS `address`,`r`.`version` AS `version`,`r`.`last_check_in` AS `last_check_in`,`r`.`attributes` AS `attributes`,`r`.`options` AS `options` from `routers` `r` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `v2_this_instance`
--

/*!50001 DROP VIEW IF EXISTS `v2_this_instance`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`root`@`localhost` SQL SECURITY INVOKER */
/*!50001 VIEW `v2_this_instance` AS select `i`.`cluster_id` AS `cluster_id`,`i`.`instance_id` AS `instance_id`,`c`.`cluster_name` AS `cluster_name`,`c`.`cluster_type` AS `cluster_type` from (`v2_instances` `i` join `clusters` `c` on((`i`.`cluster_id` = `c`.`cluster_id`))) where (convert(`i`.`mysql_server_uuid` using utf8mb4) = (select `performance_schema`.`global_variables`.`VARIABLE_VALUE` from `performance_schema`.`global_variables` where (`performance_schema`.`global_variables`.`VARIABLE_NAME` = 'server_uuid'))) */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2019-09-26 16:04:24
