@echo off
chcp 65001 >nul 2>&1

:: ===== 관리자 권한 획득 스크립트 =====
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :run_bot
) else (
    :: UAC 권한 승인 창을 띄우되, 승인 이후 새로 열리는 cmd 창은 숨김(Hidden) 처리합니다.
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WindowStyle Hidden"
    exit /b
)

:run_bot
:: 권한 상승 시 기본 경로가 System32가 되는 것을 방지
cd /d "%~dp0"

:: [System] 이전 파이썬 잔여 자식 프로세스 강제 정리
powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' -or Name='pythonw.exe'\" | Where-Object {$_.CommandLine -match 'main.py'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

if not exist "venv32\Scripts\pythonw.exe" (
    :: 콘솔창이 숨겨져 있으므로 에러 발생 시 윈도우 기본 팝업으로 안내
    msg "%USERNAME%" "에러: venv32 가상환경 파일이 누락되었거나 손상되었습니다."
    exit /b
)

:: 파이썬 자체 콘솔창 없이(pythonw) 독립 프로세스로 봇만 실행(start)하고 배치 파일은 즉시 종료
start "" ".\venv32\Scripts\pythonw.exe" main.py

exit /b
