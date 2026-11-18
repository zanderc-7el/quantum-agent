param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$rootPrefix = $projectRoot.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
$releasePath = Join-Path $projectRoot 'desktop\release.json'
function Assert-ReleasePathWithoutLinks([string]$Candidate) {
    $walk = $projectRoot
    foreach ($segment in $Candidate.Substring($rootPrefix.Length).Split([char[]]'\/')) {
        $walk = Join-Path $walk $segment
        $item = Get-Item -LiteralPath $walk -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Desktop release path must not contain junctions or symbolic links.' }
    }
}
try {
    $relativeExecutable = 'desktop\dist\win-unpacked\QuantumControl.exe'
    $expectedHash = $null
    $releaseVersion = $null
    $releaseSource = 'legacy-default'
    if (Test-Path -LiteralPath $releasePath) {
        Assert-ReleasePathWithoutLinks $releasePath
        if (-not (Test-Path -LiteralPath $releasePath -PathType Leaf)) { throw 'desktop/release.json must be a file.' }
        $release = Get-Content -LiteralPath $releasePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($release -isnot [pscustomobject] -or $release.schema -ne 1 -or
            $release.executable -isnot [string] -or [string]::IsNullOrWhiteSpace($release.executable) -or
            $release.sha256 -isnot [string] -or $release.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
            $release.version -isnot [string] -or [string]::IsNullOrWhiteSpace($release.version)) {
            throw 'desktop/release.json must define schema 1, version, relative executable and SHA256.'
        }
        $relativeExecutable = $release.executable
        $expectedHash = $release.sha256
        $releaseVersion = $release.version
        $releaseSource = 'desktop/release.json'
    }
    if ([IO.Path]::IsPathRooted($relativeExecutable) -or $relativeExecutable.Contains(':')) {
        throw 'Desktop executable must be a project-relative path.'
    }
    $desktopExecutable = [IO.Path]::GetFullPath((Join-Path $projectRoot $relativeExecutable))
    if (-not $desktopExecutable.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($desktopExecutable) -ine '.exe') {
        throw 'Desktop executable must be an EXE inside this project.'
    }
    # Refuse junction/symlink escapes before reading or launching the executable.
    Assert-ReleasePathWithoutLinks $desktopExecutable
    if (-not (Test-Path -LiteralPath $desktopExecutable -PathType Leaf)) {
        throw "Desktop package not found: $relativeExecutable. Restore the complete validated desktop package."
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [IO.File]::OpenRead($desktopExecutable)
        $actualHash = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()
    } finally {
        if ($stream) { $stream.Dispose() }
        $sha.Dispose()
    }
    if ($expectedHash -and $actualHash -ine $expectedHash) { throw 'Desktop executable SHA256 does not match desktop/release.json.' }
    if ($CheckOnly) {
        [pscustomobject]@{ProjectRoot=$projectRoot; Source=$releaseSource; Version=$releaseVersion;
            Executable=$desktopExecutable; Sha256=$actualHash; ReleaseHashVerified=[bool]$expectedHash; Started=$false}
        return
    }
    # The requested application window is visible; helper shells stay hidden.
    # This environment value applies only to the launcher and its child process.
    $env:QCAL_PROJECT_ROOT = $projectRoot
    Start-Process -FilePath $desktopExecutable -WorkingDirectory $projectRoot | Out-Null
}
catch {
    if ($CheckOnly) { throw }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($_.Exception.Message, 'Quantum Control Desktop', 'OK', 'Error') | Out-Null
    exit 1
}
