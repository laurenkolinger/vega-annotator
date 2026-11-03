# Install VEGA Annotator on Windows for this account: the app, Python, PyTorch, and a shortcut.
# Everything goes in one folder under %LOCALAPPDATA%, and no administrator rights are needed.
# With an NVIDIA card it installs PyTorch's CUDA build (about 3 GB). Otherwise it installs
# the CPU build. SAM3 comes later from the server folder, through the app's home screen.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$AppName = 'VEGA Annotator'
$UvVersion = '0.13.6'
$PythonVersion = '3.12'
$MinVramMiB = 4096
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Support = Join-Path $env:LOCALAPPDATA $AppName
$AppDir = Join-Path $Support 'app'
$Runtime = Join-Path $Support 'runtime'
$Work = Join-Path $env:TEMP ("vega-setup-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

function Step($Message) { Write-Host "`n$Message" -ForegroundColor Magenta }

# Return the chosen requirements file: CUDA with a usable NVIDIA card, CPU otherwise.
function Choose-Requirements {
  try {
    $lines = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
    if ($LASTEXITCODE -eq 0) {
      foreach ($line in $lines) {
        if ([int]($line.Trim()) -ge $MinVramMiB) { return 'windows-cuda.txt' }
      }
    }
  } catch {
    # No nvidia-smi on this computer: there is no NVIDIA driver to use.
  }
  return 'windows-cpu.txt'
}

New-Item -ItemType Directory -Force -Path $Support, $Work | Out-Null
try {
  # A running copy holds its folder open, so it stops before the files are replaced.
  try {
    Invoke-WebRequest -UseBasicParsing -Method Post -Uri 'http://127.0.0.1:5050/api/shutdown' -ContentType 'application/json' -Body '{}' -TimeoutSec 20 | Out-Null
    Start-Sleep -Seconds 2
  } catch {
    # Nothing was running.
  }

  Step 'Step 1 of 4: copying the app'
  if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
  New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
  foreach ($item in 'vega_annotator', 'vendor', 'requirements', 'VEGA Annotator.vbs', 'icon.ico') {
    Copy-Item -Recurse -Force (Join-Path $Here $item) $AppDir
  }

  Step 'Step 2 of 4: downloading the setup tool'
  $zip = Join-Path $Work 'uv.zip'
  Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
  Expand-Archive -Force -Path $zip -DestinationPath $Work
  $Uv = Join-Path $Work 'uv.exe'
  $env:UV_PYTHON_INSTALL_DIR = Join-Path $Support 'python'
  $env:UV_CACHE_DIR = Join-Path $Work 'cache'

  Step 'Step 3 of 4: downloading Python'
  if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
  & $Uv venv --python $PythonVersion --python-preference only-managed $Runtime
  if ($LASTEXITCODE -ne 0) { throw 'Python could not be installed.' }
  $Python = Join-Path $Runtime 'Scripts\python.exe'

  $Requirements = Choose-Requirements
  if ($Requirements -eq 'windows-cuda.txt') {
    Step 'Step 4 of 4: downloading PyTorch for your NVIDIA card, about 3 GB. This takes a while.'
  } else {
    Step 'Step 4 of 4: downloading PyTorch. No usable NVIDIA card was found, so SAM3 will run on the processor.'
  }
  Push-Location (Join-Path $AppDir 'requirements')
  try {
    & $Uv pip install --python $Python --index-strategy unsafe-best-match -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw 'PyTorch and its libraries could not be installed.' }
  } finally {
    Pop-Location
  }

  & $Python -c "import torch, transformers, cv2, flask; from transformers import Sam3TrackerModel; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())"
  if ($LASTEXITCODE -ne 0) { throw 'The installed libraries did not load.' }

  $Shell = New-Object -ComObject WScript.Shell
  foreach ($Folder in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) {
    $Link = $Shell.CreateShortcut((Join-Path $Folder "$AppName.lnk"))
    $Link.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $Link.Arguments = '"' + (Join-Path $AppDir 'VEGA Annotator.vbs') + '"'
    $Link.WorkingDirectory = $AppDir
    $Link.IconLocation = Join-Path $AppDir 'icon.ico'
    $Link.Description = 'VEGA annotator on this computer'
    $Link.Save()
  }

  & (Join-Path $env:WINDIR 'System32\wscript.exe') (Join-Path $AppDir 'VEGA Annotator.vbs')
} finally {
  if (Test-Path $Work) { Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue }
}
