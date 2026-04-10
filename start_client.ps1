$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$clientRoot = Join-Path $root 'client\play.pokemonshowdown.com'
Set-Location $clientRoot
python -m http.server 8080 --bind 127.0.0.1
