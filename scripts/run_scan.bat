@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  流動性與尾部風險掃描 — Windows 批次檔
rem
rem    scripts\run_scan.bat            依間隔決定要不要跑（預設 3 天）
rem    scripts\run_scan.bat --force    忽略間隔，立刻跑一次
rem
rem  設計成「可以每天叫，但只有滿 3 天才真的掃」，這樣即使排程漏跑
rem  也會在下一次補上，不會整整等到下下次。
rem ============================================================

set "REPO_DIR=%~dp0.."
pushd "%REPO_DIR%" || exit /b 1

if not defined LRC_INTERVAL_DAYS set "LRC_INTERVAL_DAYS=3"
set "STAMP=%REPO_DIR%\data\.last_scan"
set "LOG_DIR=%REPO_DIR%\logs"

set "FORCE=0"
set "EXTRA_ARGS="
for %%A in (%*) do (
  if /I "%%~A"=="--force" (set "FORCE=1") else (set "EXTRA_ARGS=!EXTRA_ARGS! %%~A")
)

rem ---- 找 python ----
set "PY="
if defined LRC_PYTHON set "PY=%LRC_PYTHON%"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo 找不到 Python，請先安裝 Python 3 並勾選 "Add to PATH"，或設定 LRC_PYTHON。
  popd & exit /b 1
)

rem ---- 間隔檢查（用 Python 算日期差，避免 batch 的日期地雷）----
if "%FORCE%"=="0" (
  if exist "%STAMP%" (
    for /f %%R in ('%PY% -c "import time,sys;p=r'%STAMP%';print(int((time.time()-float(open(p).read().strip() or 0))//86400))" 2^>nul') do set "ELAPSED=%%R"
    if defined ELAPSED (
      if !ELAPSED! LSS %LRC_INTERVAL_DAYS% (
        echo 距上次掃描 !ELAPSED! 天^（間隔設定 %LRC_INTERVAL_DAYS% 天^），本次跳過。
        popd & exit /b 0
      )
    )
  )
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%REPO_DIR%\data" mkdir "%REPO_DIR%\data"

for /f %%T in ('%PY% -c "import time;print(time.strftime('%%Y%%m%%d-%%H%%M%%S'))"') do set "TS=%%T"
set "LOG_FILE=%LOG_DIR%\scan-%TS%.log"

echo == 開始掃描 %TS% == > "%LOG_FILE%"
%PY% scan.py %EXTRA_ARGS% >> "%LOG_FILE%" 2>&1
set "STATUS=%ERRORLEVEL%"
type "%LOG_FILE%"

%PY% -c "import time;open(r'%STAMP%','w').write(str(time.time()))"

set "REPORT=%REPO_DIR%\reports\index.html"
if "%STATUS%"=="0"  echo 掃描完成，無觸發。
if "%STATUS%"=="20" echo 掃描完成，但有指標抓取失敗，詳見 %LOG_FILE%
if "%STATUS%"=="10" goto :alert
if "%STATUS%"=="30" goto :alert
goto :done

:alert
echo.
echo *** 有引信觸發，開啟報表：%REPORT%
if not "%LRC_OPEN_ON_ALERT%"=="0" start "" "%REPORT%"

:done
popd
exit /b %STATUS%
