-- MySQLShell dump 1.0.0  Distrib Ver 8.0.21 for Linux on x86_64 - for MySQL 8.0.21 (MySQL Community Server (GPL)), for Linux (x86_64)
--
-- Host: localhost    Database: stackoverflow    Table: PostHistory
-- ------------------------------------------------------
-- Server version	8.0.21

--
-- Table structure for table `PostHistory`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE IF NOT EXISTS `PostHistory` (
  `Id` int NOT NULL,
  `PostHistoryTypeId` smallint NOT NULL,
  `PostId` int NOT NULL,
  `RevisionGUID` varchar(36) NOT NULL,
  `CreationDate` datetime NOT NULL,
  `UserId` int DEFAULT NULL,
  `UserDisplayName` varchar(40) DEFAULT NULL,
  `Comment` varchar(400) DEFAULT NULL,
  `Text` text,
  PRIMARY KEY (`Id`),
  KEY `Post_history_idx_1` (`PostId`),
  KEY `Post_history_idx_2` (`UserId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
