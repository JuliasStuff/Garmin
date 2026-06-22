Add-Type -AssemblyName System.Drawing

function New-Icon {
    param([int]$Size, [string]$OutPath, [bool]$Maskable = $false)

    $bmp = New-Object System.Drawing.Bitmap($Size, $Size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

    $bgColor = [System.Drawing.ColorTranslator]::FromHtml('#1f6db8')
    $bg = New-Object System.Drawing.SolidBrush($bgColor)

    if ($Maskable) {
        $g.FillRectangle($bg, 0, 0, $Size, $Size)
    } else {
        $r = [int]($Size * 0.22)
        $gp = New-Object System.Drawing.Drawing2D.GraphicsPath
        $gp.AddArc(0, 0, $r*2, $r*2, 180, 90)
        $gp.AddArc($Size - $r*2, 0, $r*2, $r*2, 270, 90)
        $gp.AddArc($Size - $r*2, $Size - $r*2, $r*2, $r*2, 0, 90)
        $gp.AddArc(0, $Size - $r*2, $r*2, $r*2, 90, 90)
        $gp.CloseFigure()
        $g.FillPath($bg, $gp)
        $gp.Dispose()
    }

    $whitePen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, [single]($Size * 0.05))
    $whitePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $whitePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $whitePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $whiteBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)

    $safe = if ($Maskable) { 0.62 } else { 0.74 }
    $caseW = [single]($Size * $safe * 0.85)
    $caseH = [single]($Size * $safe)
    $cx = [single]($Size / 2)
    $cy = [single]($Size / 2)
    $left = [single]($cx - $caseW / 2)
    $top = [single]($cy - $caseH / 2)
    $caseR = [single]($caseW * 0.22)

    # Watch case (rounded rect outline)
    $case = New-Object System.Drawing.Drawing2D.GraphicsPath
    $case.AddArc($left, $top, $caseR*2, $caseR*2, 180, 90)
    $case.AddArc($left + $caseW - $caseR*2, $top, $caseR*2, $caseR*2, 270, 90)
    $case.AddArc($left + $caseW - $caseR*2, $top + $caseH - $caseR*2, $caseR*2, $caseR*2, 0, 90)
    $case.AddArc($left, $top + $caseH - $caseR*2, $caseR*2, $caseR*2, 90, 90)
    $case.CloseFigure()
    $g.DrawPath($whitePen, $case)
    $case.Dispose()

    # Pulse line inside the case
    $pad = [single]($caseW * 0.15)
    $lineY = [single]($cy)
    $x0 = [single]($left + $pad)
    $x1 = [single]($left + $caseW - $pad)
    $w = $x1 - $x0
    $peakH = [single]($caseH * 0.30)

    [System.Drawing.PointF[]]$pulse = @(
        [System.Drawing.PointF]::new($x0, $lineY),
        [System.Drawing.PointF]::new($x0 + $w * 0.25, $lineY),
        [System.Drawing.PointF]::new($x0 + $w * 0.38, $lineY - $peakH * 0.7),
        [System.Drawing.PointF]::new($x0 + $w * 0.50, $lineY + $peakH),
        [System.Drawing.PointF]::new($x0 + $w * 0.62, $lineY - $peakH * 0.5),
        [System.Drawing.PointF]::new($x0 + $w * 0.75, $lineY),
        [System.Drawing.PointF]::new($x1, $lineY)
    )
    $g.DrawLines($whitePen, $pulse)

    # Strap hints (top/bottom) for non-maskable icons
    if (-not $Maskable) {
        $strapW = [single]($caseW * 0.40)
        $strapH = [single]($Size * 0.045)
        $strapX = [single]($cx - $strapW / 2)
        $g.FillRectangle($whiteBrush, $strapX, $top - $strapH - $Size * 0.01, $strapW, $strapH)
        $g.FillRectangle($whiteBrush, $strapX, $top + $caseH + $Size * 0.01, $strapW, $strapH)
    }

    $bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose()
    $bmp.Dispose()
    $whitePen.Dispose()
    $whiteBrush.Dispose()
    $bg.Dispose()
    Write-Host "Wrote $OutPath"
}

$dir = Split-Path -Parent $MyInvocation.MyCommand.Definition
New-Icon -Size 192 -OutPath (Join-Path $dir 'icon-192.png') -Maskable $false
New-Icon -Size 512 -OutPath (Join-Path $dir 'icon-512.png') -Maskable $false
New-Icon -Size 512 -OutPath (Join-Path $dir 'icon-512-maskable.png') -Maskable $true
New-Icon -Size 180 -OutPath (Join-Path $dir 'apple-touch-icon.png') -Maskable $false
New-Icon -Size 32  -OutPath (Join-Path $dir 'favicon-32.png') -Maskable $false
