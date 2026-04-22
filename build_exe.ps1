$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSCommandPath
$distDir = Join-Path $projectRoot "dist"
$buildDir = Join-Path $projectRoot "build"
$exeName = "OSZ-to-BMS-Converter"

function Remove-ProjectPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $resolvedTarget = [System.IO.Path]::GetFullPath($TargetPath)
    $resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

    if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete outside the project root: $resolvedTarget"
    }

    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

Remove-ProjectPath -TargetPath $buildDir -ProjectRoot $projectRoot
Remove-ProjectPath -TargetPath $distDir -ProjectRoot $projectRoot

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $exeName `
    --distpath $distDir `
    --workpath $buildDir `
    --specpath $projectRoot `
    --collect-data om2bms `
    --collect-all PIL `
    (Join-Path $projectRoot "om2bms_osz_gui.py")

Write-Host ""
Write-Host "Build finished:"
Write-Host (Join-Path $distDir "$exeName.exe")
