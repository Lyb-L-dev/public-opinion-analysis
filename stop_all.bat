@echo off
chcp 65001 >nul
title 系统停止脚本

echo ==================== 停止所有服务 ====================

echo [1] 停止爬虫和消费者进程...
taskkill /F /FI "WINDOWTITLE eq Consumer-*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Crawler-*" >nul 2>&1

timeout /t 2 /nobreak >nul

echo [2] 停止 Kafka...
taskkill /F /FI "WINDOWTITLE eq Kafka" >nul 2>&1

timeout /t 2 /nobreak >nul

echo [3] 停止 Zookeeper...
taskkill /F /FI "WINDOWTITLE eq Zookeeper" >nul 2>&1

echo ==================== 系统已停止 ====================
echo 所有进程已关闭
pause
