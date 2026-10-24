$ErrorActionPreference = 'Stop'
$reaProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$reaRuntimePath = [IO.Path]::GetFullPath((Join-Path $reaProjectRoot 'runtime/python'))
if (-not $reaRuntimePath.StartsWith($reaProjectRoot + [IO.Path]::DirectorySeparatorChar)) { throw 'Invalid runtime scope.' }
if (Test-Path -LiteralPath $reaRuntimePath) { throw 'runtime/python already exists; refusing to overwrite it.' }
$reaDownloadDirectory = Join-Path $reaProjectRoot '.tmp'
New-Item -ItemType Directory -Path $reaDownloadDirectory -Force | Out-Null
$reaArchivePath = Join-Path $reaDownloadDirectory 'python-3.12.10-embed-amd64.zip'
$reaExpectedHash = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $reaArchivePath
if ((Get-FileHash -LiteralPath $reaArchivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $reaExpectedHash) { throw 'Python archive SHA256 mismatch.' }
Expand-Archive -LiteralPath $reaArchivePath -DestinationPath $reaRuntimePath
[IO.File]::WriteAllText((Join-Path $reaRuntimePath 'python312._pth'), "python312.zip`n.`n../../python`n../../vendor/n3ds_importer`n", [Text.Encoding]::ASCII)
Write-Output 'Project-local Python 3.12.10 is ready. No global interpreter or MCP client configuration was changed.'
