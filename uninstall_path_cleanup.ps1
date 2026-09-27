# uninstall_path_cleanup.ps1 -- Step 10 (optional add-on). Called from
# uninstall.bat, only if the user opts in there (default: no).
#
# This app never puts anything on PATH itself -- ffmpeg and (sometimes)
# Tesseract are separate installers the README tells you to add to PATH
# by hand on Windows. This finds entries that look like those two tools
# in your USER-level PATH and lets you pick which, if any, to remove.
#
# Deliberately scoped to the User environment variable only: removing
# from the Machine (system-wide) PATH needs admin rights and this script
# doesn't attempt it -- a match there is only reported, never touched.

$ErrorActionPreference = "Stop"

function Get-PathEntries($scope) {
    $raw = [Environment]::GetEnvironmentVariable("Path", $scope)
    if (-not $raw) { return @() }
    return $raw -split ";" | Where-Object { $_ -ne "" }
}

$pattern = "ffmpeg|tesseract"
$userEntries = Get-PathEntries "User"
$userMatches = @($userEntries | Where-Object { $_ -match "(?i)$pattern" })

if ($userMatches.Count -eq 0) {
    Write-Host "No ffmpeg/Tesseract-looking entries found in your user PATH."
} else {
    Write-Host "Found these entries in your user PATH:"
    for ($i = 0; $i -lt $userMatches.Count; $i++) {
        Write-Host ("  [{0}] {1}" -f ($i + 1), $userMatches[$i])
    }
    Write-Host ""
    $sel = Read-Host "Enter numbers to remove (comma-separated), 'all', or leave blank to skip"
    $sel = $sel.Trim()

    if ($sel -eq "") {
        Write-Host "Skipped -- your PATH was left unchanged."
    } else {
        if ($sel.ToLower() -eq "all") {
            $toRemove = $userMatches
        } else {
            $toRemove = @()
            foreach ($tok in ($sel -split ",")) {
                $tok = $tok.Trim()
                if ($tok -match "^[0-9]+$") {
                    $idx = [int]$tok - 1
                    if ($idx -ge 0 -and $idx -lt $userMatches.Count) {
                        $toRemove += $userMatches[$idx]
                    }
                }
            }
        }

        if ($toRemove.Count -eq 0) {
            Write-Host "Nothing recognized in that input -- your PATH was left unchanged."
        } else {
            $remaining = $userEntries | Where-Object { $toRemove -notcontains $_ }
            [Environment]::SetEnvironmentVariable("Path", ($remaining -join ";"), "User")
            Write-Host "Removed from your user PATH:"
            $toRemove | ForEach-Object { Write-Host ("  " + $_) }
            Write-Host "Open a new terminal (or sign out/in) for this to take effect elsewhere."
        }
    }
}

$machineMatches = @((Get-PathEntries "Machine") | Where-Object { $_ -match "(?i)$pattern" })
if ($machineMatches.Count -gt 0) {
    Write-Host ""
    Write-Host "Also found in your SYSTEM-wide PATH (not touched here -- needs admin rights):"
    $machineMatches | ForEach-Object { Write-Host ("  " + $_) }
    Write-Host "Remove these yourself via System Properties > Environment Variables if you want them gone."
}
