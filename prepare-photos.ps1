<#
.SYNOPSIS
Готовит фотографии шкатулок для каталога: уменьшает, поворачивает по EXIF,
переименовывает в осмысленные имена и складывает в www/.

.DESCRIPTION
Таблица ниже — это и есть привязка «какой снимок к какому товару и модификатору».
Правится здесь, а не в HTML: имена файлов на сайте собираются из ключей.

Снимки с гравировкой «С 23 февраля» (0226-0230) в таблицу намеренно не включены.
#>

param(
    [string]$Source = "C:\Users\user\Desktop\шкатулки",
    [string]$Dest = "C:\Users\user\Desktop\site-sync\www",
    [int]$MaxWidth = 1000,
    [int]$Quality = 78
)

# товар-модификатор -> список исходных снимков
$map = [ordered]@{
    'voennaya-dark'      = 'IMG_0194', 'IMG_0195', 'IMG_0196'
    'voennaya-oreh'      = 'IMG_0197'
    'voennaya-plain'     = 'IMG_0231', 'IMG_0232', 'IMG_0233'

    # классика — крупные шкатулки 0209-0223, у них есть кожа и гравировка
    'klassika-dark'       = 'IMG_0211', 'IMG_0214'
    'klassika-oreh'       = 'IMG_0210', 'IMG_0209', 'IMG_0217'
    'klassika-zamsha'     = 'IMG_0212'
    'klassika-kozha'      = 'IMG_0222', 'IMG_0223', 'IMG_0213'
    'klassika-gravirovka' = 'IMG_0216'

    # книжка — небольшие 0200-0206 с резным крестом, кожи у неё нет
    'knizhka-dark'        = 'IMG_0202', 'IMG_0203', 'IMG_0206'
    'knizhka-oreh'        = 'IMG_0200', 'IMG_0201', 'IMG_0204'
    'knizhka-zamsha'      = 'IMG_0205'

    'vydvizhnaya-plain'  = 'IMG_0218'

    'mini-dark'          = 'IMG_0224', 'IMG_0225'
}

Add-Type -AssemblyName System.Drawing

$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
    Where-Object { $_.MimeType -eq 'image/jpeg' }
$params = New-Object System.Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
    [System.Drawing.Imaging.Encoder]::Quality, [long]$Quality)

$rotations = @{
    2 = 'RotateNoneFlipX'; 3 = 'Rotate180FlipNone'; 4 = 'Rotate180FlipX'
    5 = 'Rotate90FlipX';   6 = 'Rotate90FlipNone';  7 = 'Rotate270FlipX'
    8 = 'Rotate270FlipNone'
}

$total = 0

foreach ($key in $map.Keys) {

    $index = 0
    foreach ($stem in $map[$key]) {

        $index++
        $path = Join-Path $Source "$stem.jpg"
        if (-not (Test-Path $path)) {
            Write-Warning "нет файла: $path"
            continue
        }

        $image = [System.Drawing.Image]::FromFile($path)
        try {
            if ($image.PropertyIdList -contains 274) {
                $code = [int]$image.GetPropertyItem(274).Value[0]
                if ($rotations.ContainsKey($code)) { $image.RotateFlip($rotations[$code]) }
            }

            $scale = [Math]::Min(1.0, $MaxWidth / [double]$image.Width)
            $width = [int][Math]::Round($image.Width * $scale)
            $height = [int][Math]::Round($image.Height * $scale)

            $canvas = New-Object System.Drawing.Bitmap $width, $height
            $graphics = [System.Drawing.Graphics]::FromImage($canvas)
            $graphics.InterpolationMode = 'HighQualityBicubic'
            $graphics.SmoothingMode = 'HighQuality'
            $graphics.PixelOffsetMode = 'HighQuality'
            $graphics.DrawImage($image, 0, 0, $width, $height)

            $name = "$key-$index.jpg"
            $canvas.Save((Join-Path $Dest $name), $codec, $params)

            $kb = [math]::Round((Get-Item (Join-Path $Dest $name)).Length / 1KB)
            "{0,-26} из {1}  {2}x{3}  {4} КБ" -f $name, $stem, $width, $height, $kb
            $total++
        }
        finally {
            if ($graphics) { $graphics.Dispose(); $graphics = $null }
            if ($canvas) { $canvas.Dispose(); $canvas = $null }
            $image.Dispose()
        }
    }
}

"", "Готово. Файлов: $total"
