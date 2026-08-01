<#
  安裝 Windows 工作排程器的定期掃描。

    powershell -ExecutionPolicy Bypass -File scripts\install_task_windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_task_windows.ps1 -Remove

  Windows 的排程器原生支援「每 N 天」，所以這裡直接用 -DaysInterval 3，
  不需要像 cron 那樣繞。批次檔本身還有戳記檔保險，漏跑會在下次補上。
#>

param(
  [int]$IntervalDays = 3,
  [string]$Time = "08:10",
  [switch]$Remove
)

$ErrorActionPreference = "Stop"

$repoDir  = Split-Path -Parent $PSScriptRoot
$runner   = Join-Path $repoDir "scripts\run_scan.bat"
$taskName = "LiquidityRiskConsole-Scan"

if ($Remove) {
  if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "已移除排程工作 $taskName"
  } else {
    Write-Host "沒有找到排程工作 $taskName"
  }
  return
}

if (-not (Test-Path $runner)) { throw "找不到 $runner" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c `"$runner`"" -WorkingDirectory $repoDir

$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $IntervalDays -At $Time

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -DontStopOnIdleEnd `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
  -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Settings $settings -Description "流動性與尾部風險自動掃描（每 $IntervalDays 天）" | Out-Null

Write-Host "已安裝排程：$taskName，每 $IntervalDays 天 $Time 執行"
Write-Host "  -StartWhenAvailable 代表關機錯過的話，開機後會補跑。"
Write-Host ""
Write-Host "手動跑一次： $runner --force"
Write-Host "檢視狀態：   Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo"
