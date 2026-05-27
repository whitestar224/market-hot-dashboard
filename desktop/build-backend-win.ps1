$ErrorActionPreference = 'Stop'

$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$BuildDir = Join-Path $Root '.pyinstaller-build'
$DistDir = Join-Path $Root 'dist-backend'

Set-Location $Root
python -m pip install --upgrade pyinstaller
python -m pip install -r requirements.txt

if (Test-Path $BuildDir) {
  Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
if (Test-Path $DistDir) {
  Remove-Item -LiteralPath $DistDir -Recurse -Force
}

python -m PyInstaller `
  --clean `
  --noconfirm `
  --name xingyunshe-server `
  --distpath $DistDir `
  --workpath $BuildDir `
  --specpath $BuildDir `
  server.py

Write-Host "Backend executable built under $DistDir"
