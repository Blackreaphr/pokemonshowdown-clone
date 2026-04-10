param(
    [string]$Format = 'gen9ou',
    [string]$BotName = ''
)

$ErrorActionPreference = 'Stop'

if (-not $BotName) {
    $BotName = 'Bot' + (Get-Random -Minimum 1000 -Maximum 9999)
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $root 'runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Get-ListeningProcessId {
    param([int]$Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) {
        return [int]$connection.OwningProcess
    }
    return $null
}

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

function Resolve-ExecutablePath {
    param(
        [string]$Preferred = '',
        [Parameter(Mandatory = $true)][string]$CommandName
    )
    if ($Preferred -and (Test-Path $Preferred)) {
        return (Resolve-Path -LiteralPath $Preferred).Path
    }
    $command = Get-Command $CommandName -ErrorAction Stop
    return $command.Source
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdOutPath,
        [Parameter(Mandatory = $true)][string]$StdErrPath
    )
    return Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $StdOutPath -RedirectStandardError $StdErrPath
}

function Wait-ForProcessRecord {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('server', 'client', 'bot')][string]$Kind,
        [int[]]$ExcludeProcessIds = @(),
        [int]$TimeoutSeconds = 20
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $records = @(Get-ProjectProcessRecords -Kind $Kind | Where-Object {
            $ExcludeProcessIds -notcontains [int]$_.ProcessId
        } | Sort-Object CreationDate -Descending)
        if ($records.Count -gt 0) {
            return $records[0]
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for the $Kind process to start."
}

function Wait-ForListeningPort {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 20
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $processId = Get-ListeningProcessId -Port $Port
        if ($processId) {
            return $processId
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for port $Port to accept connections."
}

$pidFile = Join-Path $runtime 'pids.json'
$existingInfo = $null
if (Test-Path $pidFile) {
    try {
        $existingInfo = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    } catch {
        $existingInfo = $null
    }
}

$serverPortPid = Get-ListeningProcessId -Port 8000
$clientPortPid = Get-ListeningProcessId -Port 8080
$existingServer = @(Get-ProjectProcessRecords -Kind 'server')
$existingClient = @(Get-ProjectProcessRecords -Kind 'client')
$existingBot = @(Get-ProjectProcessRecords -Kind 'bot')
if ($serverPortPid -or $clientPortPid -or $existingServer.Count -gt 0 -or $existingClient.Count -gt 0 -or $existingBot.Count -gt 0) {
    Write-Host 'Offline stack is already running or the required ports are already in use.'
    Write-Host 'Server: http://127.0.0.1:8000'
    Write-Host 'Client: http://127.0.0.1:8080/testclient.html?~~localhost:8000'
    if ($existingInfo -and $existingInfo.botName) {
        Write-Host "Bot guest name: $($existingInfo.botName)"
    }
    Write-Host 'Run .\stop_offline_stack.ps1 first if you want to restart it.'
    exit 0
}

$serverRoot = Join-Path $root 'server'
$clientRoot = Join-Path $root 'client\play.pokemonshowdown.com'
$preferredPython = Join-Path $root '.venv\Scripts\python.exe'
$pythonPath = Resolve-ExecutablePath -Preferred $preferredPython -CommandName 'python'
$nodePath = Resolve-ExecutablePath -CommandName 'node'

$serverOutLog = Join-Path $runtime 'server.out.log'
$serverErrLog = Join-Path $runtime 'server.err.log'
$clientOutLog = Join-Path $runtime 'client.out.log'
$clientErrLog = Join-Path $runtime 'client.err.log'
$botOutLog = Join-Path $runtime 'bot.out.log'
$botErrLog = Join-Path $runtime 'bot.err.log'

$serverBefore = @(Get-ProjectProcessRecords -Kind 'server' | ForEach-Object { [int]$_.ProcessId })
$null = Start-LoggedProcess -FilePath $nodePath -ArgumentList @('pokemon-showdown', 'start', '--no-security') `
    -WorkingDirectory $serverRoot -StdOutPath $serverOutLog -StdErrPath $serverErrLog
$serverRecord = Wait-ForProcessRecord -Kind 'server' -ExcludeProcessIds $serverBefore -TimeoutSeconds 30
$null = Wait-ForListeningPort -Port 8000 -TimeoutSeconds 30

$clientBefore = @(Get-ProjectProcessRecords -Kind 'client' | ForEach-Object { [int]$_.ProcessId })
$null = Start-LoggedProcess -FilePath $pythonPath -ArgumentList @('-m', 'http.server', '8080', '--bind', '127.0.0.1') `
    -WorkingDirectory $clientRoot -StdOutPath $clientOutLog -StdErrPath $clientErrLog
$clientRecord = Wait-ForProcessRecord -Kind 'client' -ExcludeProcessIds $clientBefore -TimeoutSeconds 15
$null = Wait-ForListeningPort -Port 8080 -TimeoutSeconds 15

$botBefore = @(Get-ProjectProcessRecords -Kind 'bot' | ForEach-Object { [int]$_.ProcessId })
$botArgs = @('-u', '-m', 'bot.bot_cli', 'accept', '--format', $Format, '--bot-name', $BotName)
$null = Start-LoggedProcess -FilePath $pythonPath -ArgumentList $botArgs `
    -WorkingDirectory $root -StdOutPath $botOutLog -StdErrPath $botErrLog
$botRecord = Wait-ForProcessRecord -Kind 'bot' -ExcludeProcessIds $botBefore -TimeoutSeconds 15

@{
    serverPid = [int]$serverRecord.ProcessId
    clientPid = [int]$clientRecord.ProcessId
    botPid = [int]$botRecord.ProcessId
    format = $Format
    botName = $BotName
} | ConvertTo-Json | Set-Content -LiteralPath $pidFile

Write-Host 'Server: http://127.0.0.1:8000'
Write-Host 'Client: http://127.0.0.1:8080/testclient.html?~~localhost:8000'
Write-Host 'Guest-only local play is enabled. Stay on the default Guest account in the browser if you want.'
Write-Host "Bot guest name: $BotName"
Write-Host "Bot format: $Format"
Write-Host "Logs: $runtime"
