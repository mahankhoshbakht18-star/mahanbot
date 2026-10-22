@echo off
chcp 65001 >nul
title MAHANBOT — Database Backup Utility
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

python -c "from core.db_migration import create_database_backup; path = create_database_backup('cbi_ultimate.db', prefix='manual_cli_backup'); print('Backup created successfully at:', path)"
pause
