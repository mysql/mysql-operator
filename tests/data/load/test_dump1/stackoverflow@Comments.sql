-- MySQLShell dump 1.0.0  Distrib Ver 8.0.21 for Linux on x86_64 - for MySQL 8.0.21 (MySQL Community Server (GPL)), for Linux (x86_64)
--
-- Host: localhost    Database: stackoverflow    Table: Comments
-- ------------------------------------------------------
-- Server version	8.0.21

--
-- Table structure for table `Comments`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE IF NOT EXISTS `Comments` (
  `Id` int NOT NULL,
  `PostId` int NOT NULL,
  `Score` int NOT NULL DEFAULT '0',
  `Text` text,
  `CreationDate` datetime DEFAULT NULL,
  `UserDisplayName` varchar(30) DEFAULT NULL,
  `UserId` int DEFAULT NULL,
  PRIMARY KEY (`Id`),
  KEY `Comments_idx_1` (`PostId`),
  KEY `Comments_idx_2` (`UserId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
