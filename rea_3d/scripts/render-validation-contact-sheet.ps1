param(
  [string]$EvidenceRoot = 'C:/rea3d-validation'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$reaOutput = Join-Path $EvidenceRoot 'generality-browser-001/contact-sheet.png'
if (Test-Path -LiteralPath $reaOutput) { throw 'Output already exists.' }
$reaCases = @(
  @('006', '리자몽'), @('009', '거북왕'), @('025', '피카츄'), @('094', '팬텀'),
  @('095', '롱스톤'), @('129', '잉어킹'), @('130', '갸라도스'), @('151', '뮤')
)
$reaBitmap = [Drawing.Bitmap]::new(1600, 684)
$reaGraphics = [Drawing.Graphics]::FromImage($reaBitmap)
$reaFont = [Drawing.Font]::new('Malgun Gothic', 15, [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)
$reaBrush = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(230, 238, 249))
try {
  $reaGraphics.Clear([Drawing.Color]::FromArgb(24, 34, 49))
  for ($reaIndex = 0; $reaIndex -lt $reaCases.Count; $reaIndex++) {
    $reaCol = $reaIndex % 4
    $reaRow = [Math]::Floor($reaIndex / 4)
    $reaX = $reaCol * 400
    $reaY = $reaRow * 342
    $reaInput = Join-Path $EvidenceRoot ('generality-browser-001/species-' + $reaCases[$reaIndex][0] + '.png')
    $reaTile = [Drawing.Image]::FromFile($reaInput)
    try { $reaGraphics.DrawImage($reaTile, [Drawing.Rectangle]::new($reaX, $reaY, 400, 300)) }
    finally { $reaTile.Dispose() }
    $reaGraphics.DrawString(($reaCases[$reaIndex][0] + ' ' + $reaCases[$reaIndex][1]), $reaFont, $reaBrush, [single]($reaX + 14), [single]($reaY + 310))
  }
  $reaBitmap.Save($reaOutput, [Drawing.Imaging.ImageFormat]::Png)
} finally {
  $reaBrush.Dispose()
  $reaFont.Dispose()
  $reaGraphics.Dispose()
  $reaBitmap.Dispose()
}
[PSCustomObject]@{Path=$reaOutput; SHA256=(Get-FileHash -LiteralPath $reaOutput -Algorithm SHA256).Hash.ToLowerInvariant()} | ConvertTo-Json
