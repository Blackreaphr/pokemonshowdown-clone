$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root 'runtime\pids.json'

function Get-ProjectProcessRecords {
    param([Parameter(Mandatory = $true)][ValidateSet('server', 'client', 'bot')][string]$Kind)
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    switch ($Kind) {
        'server' {
            return @($processes | Where-Object {
                $_.Name -eq 'node.exe' -and $_.CommandLine -like '*pokemon-showdown start --no-security*'
            })
        }
        'client' {
            return @($processes | Where-Object {
                $_.Name -eq 'python.exe' -and $_.CommandLine -like '*-m http.server 8080 --bind 127.0.0.1*'
            })
        }
        'bot' {
            return @($processes | Where-Object {
                $_.Name -eq 'python.exe' -and $_.CommandLine -like '*pokemon-showdown-offline*' -and $_.CommandLine -like '*bot.bot_cli accept*'
            })
        }
    }
}

function Get-ListeningProcessId {
    param([int]$Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) {
        return [int]$connection.OwningProcess
    }
    return $null
}

function Get-DescendantProcessIds {
    param([int]$ProcessId)
    $children = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ParentProcessId -eq $ProcessId })
    $ids = @()
    foreach ($child in $children) {
        $ids += [int]$child.ProcessId
        $ids += Get-DescendantProcessIds -ProcessId ([int]$child.ProcessId)
    }
    return $ids
}

function Stop-ProcessTree {
    param(
        [int]$ProcessId,
        [string]$Label,
        [hashtable]$Stopped
    )
    if (-not $ProcessId -or $Stopped.ContainsKey($ProcessId)) {
        return
    }
    $allIds = @(Get-DescendantProcessIds -ProcessId $ProcessId) + $ProcessId
    $allIds = @($allIds | Select-Object -Unique | Sort-Object -Descending)
    $stoppedAny = $false
    foreach ($id in $allIds) {
        if ($Stopped.ContainsKey([int]$id)) {
            continue
        }
        try {
            Stop-Process -Id $id -Force -ErrorAction Stop
            $Stopped[[int]$id] = $true
            $stoppedAny = $true
        } catch {
            $Stopped[[int]$id] = $true
        }
    }
    if ($stoppedAny) {
        Write-Host "Stopped $Label ($ProcessId)"
    }
}

$stopped = @{}
$hadAny = $false

if (Test-Path $pidFile) {
    try {
        $pids = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        foreach ($name in 'serverPid', 'clientPid', 'botPid') {
            $procId = [int]$pids.$name
            if ($procId) {
                Stop-ProcessTree -ProcessId $procId -Label $name -Stopped $stopped
                $hadAny = $true
            }
        }
    } catch {
    }
}

foreach ($record in @(Get-ProjectProcessRecords -Kind 'server')) {
    Stop-ProcessTree -ProcessId ([int]$record.ProcessId) -Label 'showdown server' -Stopped $stopped
    $hadAny = $true
}
foreach ($record in @(Get-ProjectProcessRecords -Kind 'client')) {
    Stop-ProcessTree -ProcessId ([int]$record.ProcessId) -Label 'test client' -Stopped $stopped
    $hadAny = $true
}
foreach ($record in @(Get-ProjectProcessRecords -Kind 'bot')) {
    Stop-ProcessTree -ProcessId ([int]$record.ProcessId) -Label 'bot accept process' -Stopped $stopped
    $hadAny = $true
}

foreach ($port in 8000, 8080) {
    $portPid = Get-ListeningProcessId -Port $port
    if ($portPid -and -not $stopped.ContainsKey($portPid)) {
        Stop-ProcessTree -ProcessId $portPid -Label "listener on port $port" -Stopped $stopped
        $hadAny = $true
    }
}

if (-not $hadAny) {
    Write-Host 'No tracked stack processes were found.'
}
