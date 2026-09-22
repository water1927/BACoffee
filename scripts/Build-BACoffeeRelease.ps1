param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseSource,

    [Parameter(Mandatory = $true)]
    [string]$CandidateRoot,

    [Parameter(Mandatory = $true)]
    [string]$BuildRoot,

    [string]$PythonPath = $env:BACOFFEE_BUILD_PYTHON,

    [string]$MumuManagerPath = '',

    [switch]$RunWorkerChecks,

    [switch]$RunMumuChecks,

    [switch]$RunGuiChecks
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePath = Join-Path $repositoryRoot 'src\bacoffee\BACoffee.py'
$specPath = Join-Path $repositoryRoot 'build\BACoffee.spec'
$releaseSource = (Resolve-Path $ReleaseSource).Path
$candidateParent = Split-Path -Parent $CandidateRoot

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Missing authoritative source: $sourcePath"
}
if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
    throw "Missing build spec: $specPath"
}
if ([string]::IsNullOrWhiteSpace($PythonPath) -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Missing build Python. Pass -PythonPath or set BACOFFEE_BUILD_PYTHON.'
}
if (Test-Path -LiteralPath $CandidateRoot) {
    throw "Refusing to overwrite an existing candidate directory: $CandidateRoot"
}
if (Test-Path -LiteralPath $BuildRoot) {
    throw "Refusing to overwrite an existing build directory: $BuildRoot"
}

New-Item -ItemType Directory -Path $candidateParent -Force | Out-Null
New-Item -ItemType Directory -Path $CandidateRoot -Force | Out-Null
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null

$sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToUpperInvariant()
$specHash = (Get-FileHash -LiteralPath $specPath -Algorithm SHA256).Hash.ToUpperInvariant()
$pythonVersion = (& $PythonPath --version 2>&1 | Out-String).Trim()
$buildId = "BACoffee-v1.0.1-release-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
$metadataPath = Join-Path $BuildRoot 'build-metadata.json'
$metadata = [ordered]@{
    schema = 1
    build_id = $buildId
    built_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    source_sha256 = $sourceHash
    spec_sha256 = $specHash
    python = $pythonVersion
    source_relative_path = 'src/bacoffee/BACoffee.py'
}
$metadata | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metadataPath -Encoding UTF8

Push-Location $repositoryRoot
try {
    $env:BACOFFEE_RELEASE_ROOT = $releaseSource
    $env:BACOFFEE_BUILD_METADATA = $metadataPath
    & $PythonPath -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $BuildRoot 'dist') `
        --workpath (Join-Path $BuildRoot 'work') `
        $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$builtExe = Join-Path $BuildRoot 'dist\BACoffee.exe'
if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw "Build output is missing: $builtExe"
}

$excludedRoots = @('data', 'user_assets')
foreach ($item in Get-ChildItem -LiteralPath $releaseSource -Force) {
    if ($excludedRoots -contains $item.Name) {
        continue
    }
    Copy-Item -LiteralPath $item.FullName -Destination $CandidateRoot -Recurse -Force
}
$candidateData = Join-Path $CandidateRoot 'data'
$candidateStudents = Join-Path $CandidateRoot 'user_assets\students'
New-Item -ItemType Directory -Path $candidateData -Force | Out-Null
New-Item -ItemType Directory -Path $candidateStudents -Force | Out-Null
$sourceReadme = Join-Path $releaseSource 'user_assets\students\README.txt'
if (Test-Path -LiteralPath $sourceReadme -PathType Leaf) {
    Copy-Item -LiteralPath $sourceReadme -Destination $candidateStudents -Force
}
$candidateSource = Join-Path $CandidateRoot 'BACoffee.py'
$candidateExe = Join-Path $CandidateRoot 'BACoffee.exe'
$candidateMetadata = Join-Path $CandidateRoot 'build-metadata.json'
$candidateManifest = Join-Path $CandidateRoot 'release-manifest.json'
Copy-Item -LiteralPath $sourcePath -Destination $candidateSource -Force
Copy-Item -LiteralPath $builtExe -Destination $candidateExe -Force
Copy-Item -LiteralPath $metadataPath -Destination $candidateMetadata -Force
if (Test-Path -LiteralPath $candidateManifest) {
    Remove-Item -LiteralPath $candidateManifest -Force
}

if ($MumuManagerPath) {
    if (-not (Test-Path -LiteralPath $MumuManagerPath -PathType Leaf)) {
        throw "The supplied test MuMuManager.exe does not exist: $MumuManagerPath"
    }
    $candidateSettings = Join-Path $candidateData 'settings.json'
    $settings = [ordered]@{
        settings_version = 9
        mumu_manager_path = (Resolve-Path -LiteralPath $MumuManagerPath).Path
        mumu_vm_index = 1
        mumu_android_version = '15'
    }
    $settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $candidateSettings -Encoding UTF8
}

$candidateSourceHash = (Get-FileHash -LiteralPath $candidateSource -Algorithm SHA256).Hash.ToUpperInvariant()
if ($candidateSourceHash -ne $sourceHash) {
    throw "Candidate source hash mismatch: source=$sourceHash candidate=$candidateSourceHash"
}

# The self-test may update data/ and user_assets/.  Run it before generating
# the immutable-file manifest, and keep mutable state outside that manifest.
$selfTest = Start-Process -FilePath $candidateExe -ArgumentList '--self-test' `
    -WorkingDirectory $CandidateRoot -Wait -PassThru -WindowStyle Hidden
if ($selfTest.ExitCode -ne 0) {
    throw "Candidate self-test failed with exit code $($selfTest.ExitCode)"
}

if ($RunWorkerChecks) {
    $workerOut = Join-Path $candidateData 'release-worker.stdout.txt'
    $workerErr = Join-Path $candidateData 'release-worker.stderr.txt'
    $worker = Start-Process -FilePath $candidateExe -ArgumentList '--worker' `
        -WorkingDirectory $CandidateRoot -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr
    $scheduledOut = Join-Path $candidateData 'release-scheduled.stdout.txt'
    $scheduledErr = Join-Path $candidateData 'release-scheduled.stderr.txt'
    $scheduled = Start-Process -FilePath $candidateExe -ArgumentList '--worker', '--scheduled' `
        -WorkingDirectory $CandidateRoot -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $scheduledOut -RedirectStandardError $scheduledErr
    Write-Output "worker_exit=$($worker.ExitCode)"
    Write-Output "scheduled_worker_exit=$($scheduled.ExitCode)"
}

if ($RunMumuChecks) {
    $mumuOut = Join-Path $candidateData 'release-mumu.stdout.txt'
    $mumuErr = Join-Path $candidateData 'release-mumu.stderr.txt'
    $mumu = Start-Process -FilePath $candidateExe -ArgumentList '--test-mumu-environment' `
        -WorkingDirectory $CandidateRoot -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $mumuOut -RedirectStandardError $mumuErr
    Write-Output "mumu_test_exit=$($mumu.ExitCode)"
}

if ($RunGuiChecks) {
    $existingGui = @(Get-Process -Name BACoffee -ErrorAction SilentlyContinue)
    if ($existingGui.Count -gt 0) {
        throw 'Refusing GUI check while another BACoffee process is already running.'
    }
    $launched = @()
    1..6 | ForEach-Object {
        $launched += (Start-Process -FilePath $candidateExe -PassThru).Id
    }
    Start-Sleep -Seconds 8
    $runningGui = @(Get-Process -Name BACoffee -ErrorAction SilentlyContinue)
    $visibleGui = @($runningGui | Where-Object { $_.MainWindowHandle -ne 0 })
    Write-Output "gui_launched_count=$($launched.Count)"
    Write-Output "gui_running_process_count=$($runningGui.Count)"
    Write-Output "gui_visible_window_process_count=$($visibleGui.Count)"
    $runningIds = @($runningGui | ForEach-Object { $_.Id })
    if ($runningIds.Count -gt 0) {
        Stop-Process -Id $runningIds -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
    Write-Output "gui_remaining_after_cleanup=$(@(Get-Process -Name BACoffee -ErrorAction SilentlyContinue).Count)"
}

# Do not ship test logs, local settings, BAAS profiles, or machine-specific
# MuMu paths.  The clean candidate is intentionally ready for first-run setup.
if (Test-Path -LiteralPath $candidateData) {
    Remove-Item -LiteralPath $candidateData -Recurse -Force
}
New-Item -ItemType Directory -Path $candidateData -Force | Out-Null

function Get-RelativeUnixPath([string]$Root, [string]$Path) {
    return $Path.Substring($Root.Length).TrimStart('\').Replace('\', '/')
}

$allFiles = @(Get-ChildItem -LiteralPath $CandidateRoot -File -Recurse)
$mutablePrefixes = @('data/', 'user_assets/')
$manifestFiles = @(
    foreach ($file in $allFiles) {
        $relative = Get-RelativeUnixPath $CandidateRoot $file.FullName
        if ($relative -eq 'release-manifest.json') {
            continue
        }
        if ($mutablePrefixes | Where-Object { $relative.StartsWith($_, [System.StringComparison]::OrdinalIgnoreCase) }) {
            continue
        }
        [ordered]@{
            path = $relative
            size = [int64]$file.Length
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        }
    }
)
$candidateExeHash = (Get-FileHash -LiteralPath $candidateExe -Algorithm SHA256).Hash.ToUpperInvariant()
$manifest = [ordered]@{
    schema = 2
    kind = 'release-candidate'
    build_id = $buildId
    built_at_utc = $metadata.built_at_utc
    source_sha256 = $sourceHash
    candidate_source_sha256 = $candidateSourceHash
    spec_sha256 = $specHash
    exe = [ordered]@{
        path = 'BACoffee.exe'
        size = [int64](Get-Item -LiteralPath $candidateExe).Length
        sha256 = $candidateExeHash
    }
    candidate_file_count = $allFiles.Count + 1
    immutable_file_count = $manifestFiles.Count
    mutable_roots_excluded = $mutablePrefixes
    files = $manifestFiles
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $candidateManifest -Encoding UTF8

$finalManifest = Get-Content -LiteralPath $candidateManifest -Raw | ConvertFrom-Json
if ($finalManifest.source_sha256 -ne $sourceHash -or
    $finalManifest.candidate_source_sha256 -ne $candidateSourceHash -or
    $finalManifest.exe.sha256 -ne $candidateExeHash) {
    throw 'Candidate manifest does not match the assembled candidate.'
}

Write-Output "build_id=$buildId"
Write-Output "source_sha256=$sourceHash"
Write-Output "candidate_source_sha256=$candidateSourceHash"
Write-Output "exe_sha256=$candidateExeHash"
Write-Output "candidate_root=$CandidateRoot"
Write-Output "manifest=$candidateManifest"
