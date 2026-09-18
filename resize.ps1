<#
.SYNOPSIS
Уменьшает фотографии и пересохраняет в JPEG, учитывая поворот из EXIF.

.DESCRIPTION
Снимки с телефона весят по 2-3 МБ и часто содержат метку поворота: без её учёта
портретное фото ложится на бок. Здесь метка применяется, а данные EXIF в
результат не попадают — на сайте не нужны ни геометки, ни модель телефона.

.EXAMPLE
.\resize.ps1 -Source "C:\...\шкатулки" -Dest "C:\...\thumbs" -MaxWidth 320 -Quality 70
#>

param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Dest,
    [int]$MaxWidth = 1400,
    [int]$Quality = 82,
    [string]$Filter = '*.jpg'
)

Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $Dest)) {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
}

$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
    Where-Object { $_.MimeType -eq 'image/jpeg' }
$params = New-Object System.Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
    [System.Drawing.Imaging.Encoder]::Quality, [long]$Quality)

# EXIF-тег 274: как повернуть кадр, снятый боком
$rotations = @{
    2 = 'RotateNoneFlipX'; 3 = 'Rotate180FlipNone'; 4 = 'Rotate180FlipX'
    5 = 'Rotate90FlipX';   6 = 'Rotate90FlipNone';  7 = 'Rotate270FlipX'
    8 = 'Rotate270FlipNone'
}

foreach ($file in Get-ChildItem -Path $Source -Filter $Filter -File) {

    $image = [System.Drawing.Image]::FromFile($file.FullName)
    try {
        if ($image.PropertyIdList -contains 274) {
            $code = $image.GetPropertyItem(274).Value[0]
            if ($rotations.ContainsKey([int]$code)) {
                $image.RotateFlip($rotations[[int]$code])
            }
        }

        $scale = [Math]::Min(1.0, $MaxWidth / [double]$image.Width)
        $width = [int]([Math]::Round($image.Width * $scale))
        $height = [int]([Math]::Round($image.Height * $scale))

        $canvas = New-Object System.Drawing.Bitmap $width, $height
        $graphics = [System.Drawing.Graphics]::FromImage($canvas)
        $graphics.InterpolationMode = 'HighQualityBicubic'
        $graphics.SmoothingMode = 'HighQuality'
        $graphics.PixelOffsetMode = 'HighQuality'
        $graphics.DrawImage($image, 0, 0, $width, $height)

        $target = Join-Path $Dest ($file.BaseName + '.jpg')
        $canvas.Save($target, $codec, $params)

        $kb = [math]::Round((Get-Item $target).Length / 1KB)
        "{0,-16} {1,5}x{2,-5} {3,6} КБ" -f $file.Name, $width, $height, $kb
    }
    finally {
        if ($graphics) { $graphics.Dispose() }
        if ($canvas) { $canvas.Dispose() }
        $image.Dispose()
    }
}
