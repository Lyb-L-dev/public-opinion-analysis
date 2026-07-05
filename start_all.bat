@echo off
title System Startup Script

echo ==================== Starting System ====================
echo.

echo [1/6] Starting Zookeeper...
start "Zookeeper" cmd /k "cd /d E:\kafka\kafka_2.13-3.9.1 && bin\windows\zookeeper-server-start.bat config\zookeeper.properties"

echo Waiting for Zookeeper (10s)...
timeout /t 10 /nobreak >nul

echo [2/6] Starting Kafka...
start "Kafka" cmd /k "cd /d E:\kafka\kafka_2.13-3.9.1 && bin\windows\kafka-server-start.bat config\server.properties"

echo Waiting for Kafka (15s)...
timeout /t 15 /nobreak >nul

echo ==================== System Started ====================
echo All components started in separate windows
echo Check each window for output
pause
