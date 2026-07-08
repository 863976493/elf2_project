@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Real ESP32 MQTT input only. Do not start mock_sender.py or simulator scripts here.
rem MQTT_BROKER must be the same Mosquitto broker IP used by both ESP32 sketches.
rem If Mosquitto runs on this PC, keep 127.0.0.1. If it runs on teammate PC, change it to that PC LAN IP.
set MQTT_BROKER=127.0.0.1
set MQTT_PORT=1883
set MQTT_ENABLED=true

python start_server.py
pause