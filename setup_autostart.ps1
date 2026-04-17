# ============================================================
# LLM 서버 Windows 부팅 시 자동 시작 등록
# 관리자 PowerShell에서 실행: .\setup_autostart.ps1
# ============================================================

$TaskName = "VLLMOrchestratorLLMServer"
$ScriptPath = "C:\Users\SUZZI\Downloads\LLM\start_llm_server.bat"

# 기존 작업 삭제 (있으면)
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# 트리거: 로그온 시
$Trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME

# 액션: start_llm_server.bat 실행
$Action = New-ScheduledTaskAction -Execute $ScriptPath -WorkingDirectory "C:\Users\SUZZI\Downloads\LLM"

# 설정: 숨겨서 실행, 실패 시 1분 후 재시도
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 3

Register-ScheduledTask -TaskName $TaskName -Trigger $Trigger -Action $Action -Settings $Settings -Description "vLLM GPU server (Qwen2.5-7B-Instruct-AWQ on RTX 5070)" -RunLevel Highest

Write-Host ""
Write-Host "Task Scheduler registered: $TaskName" -ForegroundColor Green
Write-Host "  Trigger: At logon ($env:USERNAME)"
Write-Host "  Action:  $ScriptPath"
Write-Host "  Retry:   1min interval, 3 attempts"
Write-Host ""
Write-Host "To test: schtasks /run /tn $TaskName" -ForegroundColor Cyan
Write-Host "To remove: Unregister-ScheduledTask -TaskName $TaskName" -ForegroundColor DarkGray
