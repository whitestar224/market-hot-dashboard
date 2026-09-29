@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  chain_ecosystem.db 安全清理（一键）
echo ============================================
echo.
echo 此操作会：停服 -^> 备份数据库 -^> 淘汰 14 天前旧数据 -^> VACUUM 压缩
echo 预计释放约 200 MB（当前 617 MB -^> 约 420 MB）
echo.
set /p CONFIRM="确认继续？(输入 y 继续，其他任意键取消): "
if /i not "%CONFIRM%"=="y" (
    echo 已取消。
    pause
    exit /b 0
)

echo.
echo [1/4] 停止后台服务 ...
python service_guard.py stop
echo 等待旧守护完全退出（30 秒）...
timeout /t 30 /nobreak >nul

echo [2/4] 淘汰旧数据 + VACUUM（含备份）...
python prune_chain_ecosystem.py --backup
if errorlevel 1 (
    echo.
    echo [错误] 清理失败，数据库可能未改动。请检查上方报错。
    echo 备份已生成（若到备份步骤），可手动重启服务。
) else (
    echo.
    echo [3/4] 清理完成。
)

echo.
echo [4/4] 重新启动后台服务 ...
python service_guard.py start
echo.
echo 完成。可打开 http://127.0.0.1:8765/ 检查页面。
echo.
pause
endlocal
