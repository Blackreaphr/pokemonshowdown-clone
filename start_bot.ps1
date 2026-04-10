param(
    [string]$Format = 'gen9ou',
    [string]$BotName = '',
    [string]$Opponent
)

$ErrorActionPreference = 'Stop'

if (-not $BotName) {
    $BotName = 'Bot' + (Get-Random -Minimum 1000 -Maximum 9999)
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
$arguments = @('-u', '-m', 'bot.bot_cli', 'accept', '--format', $Format, '--bot-name', $BotName)
if ($Opponent) {
    $arguments += @('--opponent', $Opponent)
}
Write-Host "Bot guest name: $BotName"
& $python @arguments
