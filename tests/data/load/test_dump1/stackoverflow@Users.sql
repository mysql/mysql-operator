-- MySQLShell dump 1.0.0  Distrib Ver 8.0.21 for Linux on x86_64 - for MySQL 8.0.21 (MySQL Community Server (GPL)), for Linux (x86_64)
--
-- Host: localhost    Database: stackoverflow    Table: Users
-- ------------------------------------------------------
-- Server version	8.0.21

--
-- Table structure for table `Users`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE IF NOT EXISTS `Users` (
  `Id` int NOT NULL,
  `Reputation` int NOT NULL,
  `CreationDate` datetime DEFAULT NULL,
  `DisplayName` varchar(40) DEFAULT NULL,
  `LastAccessDate` datetime NOT NULL,
  `WebsiteUrl` varchar(256) DEFAULT NULL,
  `Location` varchar(256) DEFAULT NULL,
  `AboutMe` text,
  `Views` int DEFAULT '0',
  `UpVotes` int DEFAULT NULL,
  `DownVotes` int DEFAULT NULL,
  `ProfileImageUrl` varchar(200) DEFAULT NULL,
  `EmailHash` varchar(32) DEFAULT NULL,
  `Age` int DEFAULT NULL,
  `AccountId` int DEFAULT NULL,
  PRIMARY KEY (`Id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
