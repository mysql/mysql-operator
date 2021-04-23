-- MySQLShell dump 1.0.0  Distrib Ver 8.0.21 for Linux on x86_64 - for MySQL 8.0.21 (MySQL Community Server (GPL)), for Linux (x86_64)
--
-- Host: localhost    Database: stackoverflow    Table: Badges
-- ------------------------------------------------------
-- Server version	8.0.21

--
-- Table structure for table `Badges`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE IF NOT EXISTS `Badges` (
  `Id` int NOT NULL,
  `UserId` int DEFAULT NULL,
  `Name` varchar(50) DEFAULT NULL,
  `Date` datetime DEFAULT NULL,
  `Class` smallint DEFAULT NULL,
  `TagBased` bit(64) DEFAULT NULL,
  PRIMARY KEY (`Id`),
  KEY `Badges_idx_1` (`UserId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
