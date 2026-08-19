@echo off
chcp 65001 >nul
title 비트코인 선물 자동매매 - 배포용 EXE 빌드
setlocal enabledelayedexpansion

echo ============================================================
echo   배포용 EXE 빌드  (HedgedMartingaleBot.exe)
echo ============================================================
echo.

cd /d "%~dp0"

REM ── 1) 필요한 파일 확인 ──────────────────────────────────
if not exist "hedged_martingale_bot.py" (
    echo [오류] hedged_martingale_bot.py 가 이 폴더에 없습니다.
    echo        엔진 파일과 화면 파일이 같은 폴더에 있어야 합니다.
    goto :fail
)
if not exist "hedged_martingale_bot_gui.py" (
    echo [오류] hedged_martingale_bot_gui.py 가 이 폴더에 없습니다.
    goto :fail
)

REM ── 2) 파이썬 확인 ──────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] 파이썬이 설치되어 있지 않거나 PATH에 없습니다.
    echo        https://www.python.org/downloads/ 에서 설치할 때
    echo        "Add Python to PATH" 를 반드시 체크하세요.
    goto :fail
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [1/5] %%v 확인됨

REM ── 3) 필요한 패키지 설치 ────────────────────────────────
echo [2/5] 필요한 패키지 설치 중... (처음 한 번은 몇 분 걸립니다)
python -m pip install --upgrade pip >nul 2>&1
python -m pip install --quiet pyinstaller ccxt requests certifi
if errorlevel 1 (
    echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
    goto :fail
)
echo       설치 완료

REM ── 4) 예전 빌드 찌꺼기 삭제 (이게 남아 있으면 수정이 반영 안 됨) ──
echo [3/5] 예전 빌드 정리 중...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "HedgedMartingaleBot.spec" del /q "HedgedMartingaleBot.spec"
if exist "__pycache__" rmdir /s /q "__pycache__"
echo       정리 완료

REM ── 5) 빌드 ────────────────────────────────────────────
echo [4/5] EXE 빌드 중... (5~10분 걸립니다. 창을 닫지 마세요)
python -m PyInstaller --onefile --noconsole ^
    --name "HedgedMartingaleBot" ^
    --collect-all ccxt ^
    --collect-all certifi ^
    hedged_martingale_bot_gui.py
if errorlevel 1 (
    echo [오류] 빌드에 실패했습니다. 위에 나온 메시지를 확인하세요.
    goto :fail
)

REM ── 6) 결과 확인 ───────────────────────────────────────
if not exist "dist\HedgedMartingaleBot.exe" (
    echo [오류] 빌드는 끝났는데 exe 파일이 만들어지지 않았습니다.
    goto :fail
)
echo [5/5] 빌드 완료

echo.
echo ============================================================
echo   완료!  dist\HedgedMartingaleBot.exe
echo ============================================================
for %%A in ("dist\HedgedMartingaleBot.exe") do echo   파일 크기 : %%~zA 바이트
echo.
echo   배포 전 확인할 것
echo    1) dist\HedgedMartingaleBot.exe 를 실행해서 창이 뜨는지
echo    2) 로그 첫 줄의 버전 번호가 최신인지
echo    3) 소액으로 하루 돌려보고 로그가 정상인지
echo.
echo   배포할 파일은 dist\HedgedMartingaleBot.exe 하나면 됩니다.
echo.
pause
exit /b 0

:fail
echo.
echo ------------------------------------------------------------
echo   빌드가 중단되었습니다.
echo ------------------------------------------------------------
pause
exit /b 1
