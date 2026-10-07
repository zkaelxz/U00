<#
.SYNOPSIS
Starts llama-server with Qwen3.6-35B-A3B, then OpenCode in the repo root.
Run from PowerShell: .\tools\start-local-coder.ps1 [-LlamaDir E:\llama] [-Terminal]
Opens OpenCode's web UI in the browser; -Terminal uses the terminal UI instead.
Press Ctrl+C (web) or exit OpenCode (terminal) to stop both programs.
#>
param(
    [string]$LlamaDir = 'E:\llama',
    [int]$TimeoutSec = 1800,
    [switch]$Terminal
)

$ErrorActionPreference = 'Stop'
$port = 8080
$repoRoot = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $LlamaDir 'llama-server.exe'

if (-not (Test-Path $exe)) { throw "llama-server.exe not found at $exe (use -LlamaDir)" }
if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    throw 'opencode not found; install it with: npm i -g opencode-ai'
}
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port is already listening; a server is already running."
}

$cores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum

# --n-cpu-moe 99 keeps every MoE expert tensor in system RAM so the 12 GB GPU only
# holds attention and the KV cache. -b/-ub 2048 speeds up prompt processing, which
# dominates agent turns because each one resends a long context.
$serverArgs = @(
    '-hf', 'unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M',
    '--fit', 'off', '-ngl', '99', '--n-cpu-moe', '99',
    '-c', '65536', '-fa', 'on', '-ctk', 'q8_0', '-ctv', 'q8_0',
    '-t', $cores, '-b', '2048', '-ub', '2048',
    '--reasoning', 'off', '--jinja', '--port', $port
)

$server = Start-Process -FilePath $exe -ArgumentList $serverArgs -PassThru
try {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($server.HasExited) { throw "llama-server exited with code $($server.ExitCode)" }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $ready) { throw "llama-server not healthy after $TimeoutSec s" }

    Push-Location $repoRoot
    try {
        # Fixed port so the browser address and added project stay the same between runs.
        if ($Terminal) { opencode } else { opencode web --port 4096 }
    } finally { Pop-Location }
} finally {
    # Frees the GPU/RAM the model holds once the session ends.
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
