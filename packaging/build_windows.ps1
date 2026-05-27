$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FfmpegDir = Join-Path $ProjectRoot "resources\ffmpeg\win64"
$DistDir = Join-Path $ProjectRoot "dist\YKVTransform"
$SpecPath = Join-Path $PSScriptRoot "ykv_transform.spec"

Write-Host "Project root: $ProjectRoot"

function Ensure-Ffmpeg {
    $ffmpegExe = Join-Path $FfmpegDir "ffmpeg.exe"
    $ffprobeExe = Join-Path $FfmpegDir "ffprobe.exe"
    if ((Test-Path $ffmpegExe) -and (Test-Path $ffprobeExe)) {
        Write-Host "FFmpeg already present."
        return
    }

    New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null
    $zipPath = Join-Path $env:TEMP "ffmpeg-win64-gpl.zip"
    $extractPath = Join-Path $env:TEMP "ffmpeg-win64-gpl"

    if (Test-Path $extractPath) {
        Remove-Item $extractPath -Recurse -Force
    }

    $releaseUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    Write-Host "Downloading FFmpeg from $releaseUrl"
    Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

    $binDir = Get-ChildItem -Path $extractPath -Directory | ForEach-Object {
        Join-Path $_.FullName "bin"
    } | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $binDir) {
        throw "Unable to locate ffmpeg bin directory in downloaded archive."
    }

    Copy-Item (Join-Path $binDir "ffmpeg.exe") $ffmpegExe -Force
    Copy-Item (Join-Path $binDir "ffprobe.exe") $ffprobeExe -Force
    Copy-Item (Join-Path $binDir "LICENSE") (Join-Path $FfmpegDir "FFmpeg_LICENSE.txt") -Force -ErrorAction SilentlyContinue
    Write-Host "FFmpeg installed to $FfmpegDir"
}

function Build-App {
    Push-Location $ProjectRoot
    try {
        python -m pip install --upgrade pip
        python -m pip install pyinstaller pyside6
        if (Test-Path $DistDir) {
            Remove-Item $DistDir -Recurse -Force
        }
        python -m PyInstaller --noconfirm $SpecPath
    } finally {
        Pop-Location
    }
}

function Build-Installer {
    $isccCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        Write-Warning "Inno Setup not found. Skipping installer build."
        Write-Warning "Install Inno Setup 6 and rerun this script to produce YKVTransform-Setup-x64.exe"
        return
    }

    $issPath = Join-Path $PSScriptRoot "installer.iss"
    & $iscc $issPath
    Write-Host "Installer created under dist\installer"
}

Ensure-Ffmpeg
Build-App
Build-Installer

Write-Host "Done."
