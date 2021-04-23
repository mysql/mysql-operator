-- MySQLShell dump 1.0.0  Distrib Ver 8.0.21 for Linux on x86_64 - for MySQL 8.0.21 (MySQL Community Server (GPL)), for Linux (x86_64)
--
-- Host: localhost    Database: stackoverflow    Table: Posts
-- ------------------------------------------------------
-- Server version	8.0.21

--
-- Table structure for table `Posts`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE IF NOT EXISTS `Posts` (
  `Id` int NOT NULL,
  `PostTypeId` tinyint NOT NULL,
  `AcceptedAnswerId` int DEFAULT NULL,
  `ParentId` int DEFAULT NULL,
  `CreationDate` datetime NOT NULL,
  `DeletionDate` datetime DEFAULT NULL,
  `Score` int DEFAULT NULL,
  `ViewCount` int DEFAULT NULL,
  `Body` text,
  `OwnerUserId` int DEFAULT NULL,
  `OwnerDisplayName` varchar(256) DEFAULT NULL,
  `LastEditorUserId` int DEFAULT NULL,
  `LastEditorDisplayName` varchar(40) DEFAULT NULL,
  `LastEditDate` datetime DEFAULT NULL,
  `LastActivityDate` datetime DEFAULT NULL,
  `Title` varchar(256) DEFAULT NULL,
  `Tags` varchar(256) DEFAULT NULL,
  `AnswerCount` int DEFAULT '0',
  `CommentCount` int DEFAULT '0',
  `FavoriteCount` int DEFAULT '0',
  `ClosedDate` datetime DEFAULT NULL,
  `CommunityOwnedDate` datetime DEFAULT NULL,
  PRIMARY KEY (`Id`),
  KEY `Posts_idx_1` (`AcceptedAnswerId`),
  KEY `Posts_idx_2` (`ParentId`),
  KEY `Posts_idx_3` (`OwnerUserId`),
  KEY `Posts_idx_4` (`LastEditorUserId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
