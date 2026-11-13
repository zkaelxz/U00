# Runs elevated (Setup's service step): copies Setup's service files into an
# admin-only folder, checks the COPY against the digest compiled into Setup,
# and only then runs service.py from that copy. Setup checks this script's own
# text against a hash compiled into it, puts the values below in front of it
# and passes the whole text to PowerShell, so none of it is read from a folder
# the user can change after the check. ASCII only: Setup converts it byte by byte.
#
# Set by Setup above this line:
#   $Source   Setup's extracted service files (user-writable, so untrusted)
#   $Staging  an admin-only folder next to the admin folder, in Program Files
#   $Digest   the tree digest compiled into Setup (build_installer.service_tree_digest)
#   $Root $Data $Extra   the install folder, the data folder and optional service.py options
$ErrorActionPreference = 'Stop'
# The elevated process inherits the user's module path; only the system's modules may load here.
$env:PSModulePath = Join-Path $PSHOME 'Modules'

function Stop-Setup($Why, $Code) {
    Write-Host $Why
    if (Test-Path -LiteralPath $Staging) {
        Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit $Code
}

trap { Stop-Setup ('Unexpected error: ' + $_) 4 }

# One line per file, "<path with />", a tab, the file's SHA-256 in lower case,
# a line feed, sorted by ordinal path; the digest is the SHA-256 of that text.
function Get-TreeDigest($Dir) {
    $base = (Get-Item -LiteralPath $Dir).FullName.TrimEnd('\')
    $map = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -Force -File) {
        $rel = $f.FullName.Substring($base.Length + 1).Replace('\', '/')
        $map[$rel] = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $keys = [string[]]$map.Keys
    [Array]::Sort($keys, [StringComparer]::Ordinal)
    $text = New-Object System.Text.StringBuilder
    foreach ($k in $keys) {
        [void]$text.Append($k).Append([char]9).Append($map[$k]).Append([char]10)
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text.ToString())
    $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
}

# Walks the source itself and stops at any link, instead of letting Copy-Item follow one swapped in
# after the check below.
function Copy-Unlinked($From, $To) {
    foreach ($item in Get-ChildItem -LiteralPath $From -Force) {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            Stop-Setup 'Setup''s service files contain a link, so nothing was installed.' 3
        }
        $target = Join-Path $To $item.Name
        if ($item.PSIsContainer) {
            New-Item -ItemType Directory -Path $target | Out-Null
            Copy-Unlinked $item.FullName $target
        } else {
            Copy-Item -LiteralPath $item.FullName -Destination $target
        }
    }
}

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    Stop-Setup 'Setup''s service files are missing.' 3
}
$linked = Get-ChildItem -LiteralPath $Source -Recurse -Force |
    Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint }
if ($linked) {
    Stop-Setup 'Setup''s service files contain a link, so nothing was installed.' 3
}

if (Test-Path -LiteralPath $Staging) {
    $old = Get-Item -LiteralPath $Staging -Force
    if ($old.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        $old.Delete()
    } else {
        Remove-Item -LiteralPath $Staging -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $Staging | Out-Null
# Program Files lets Users read what is created in it; nothing of the copy may be readable or
# changeable by them, whatever the source turns out to hold (S-1-5-18 SYSTEM, S-1-5-32-544 Administrators).
& icacls.exe $Staging /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-Setup 'The staging folder could not be locked down.' 3
}
Copy-Unlinked $Source $Staging

if ((Get-TreeDigest $Staging) -ne $Digest) {
    Stop-Setup 'Setup''s service files do not match this installer, so nothing was installed.' 3
}

$python = Join-Path $Staging 'helper\python\python.exe'
$service = Join-Path $Staging 'helper\lib\installer\service.py'
$arguments = '-I -S "' + $service + '" --install-root "' + $Root + '" --data-dir "' + $Data + '" install' + $Extra
$run = Start-Process -FilePath $python -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
$code = $run.ExitCode
Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
# Start-Process can leave ExitCode empty, and "exit $null" would report success.
if ($code -isnot [int]) {
    Write-Host 'The service step gave no exit code.'
    exit 5
}
exit $code
