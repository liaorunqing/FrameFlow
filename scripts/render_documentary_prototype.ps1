param([string]$ProjectId = "507f6430-e5ce-4c8f-8a70-220aa907d99d")

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$artifact = Join-Path $root "backend\data\workflow-artifacts\$ProjectId"
$ffmpeg = (Get-Command ffmpeg).Source
$images = @("kf-000-attempt-02.jpg", "kf-002-attempt-01.jpg", "kf-003-attempt-01.jpg", "kf-004-attempt-01.jpg", "kf-005-attempt-01.jpg")
$segments = @()

for ($index = 0; $index -lt $images.Count; $index++) {
    $input = Join-Path $artifact $images[$index]
    $segment = Join-Path $artifact ("documentary-segment-{0:D2}.mp4" -f ($index + 1))
    $direction = if ($index % 2 -eq 0) { "z='min(zoom+0.00045,1.065)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'" } else { "z='min(zoom+0.00035,1.05)':x='iw/2-(iw/zoom/2)+8*sin(on/40)':y='ih/2-(ih/zoom/2)'" }
    & $ffmpeg -y -loop 1 -i $input -vf "zoompan=$($direction):d=144:s=720x1280:fps=24,format=yuv420p" -t 6 -r 24 -c:v libx264 -preset medium -crf 18 -an $segment 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Segment render failed: $($index + 1)" }
    $segments += $segment
}

$concat = Join-Path $artifact "documentary-concat.txt"
$segments | ForEach-Object { "file '$($_.Replace('\', '/'))'" } | Set-Content -Encoding ascii $concat
$silent = Join-Path $artifact "documentary-picture-lock.mp4"
& $ffmpeg -y -f concat -safe 0 -i $concat -c copy $silent 2>$null
if ($LASTEXITCODE -ne 0) { throw "Segment concat failed." }

$narration = Join-Path $artifact "narration.wav"
$final = Join-Path $artifact "documentary-prototype-30s.mp4"
Push-Location $artifact
try {
    & $ffmpeg -y -i $silent -i $narration -f lavfi -t 30 -i "anoisesrc=color=pink:amplitude=0.0025:sample_rate=48000" -filter_complex "[0:v]subtitles='subtitles.srt':force_style='FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=92'[v];[1:a]volume=1.0[n];[2:a]highpass=f=100,lowpass=f=5500,volume=0.65[room];[n][room]amix=inputs=2:duration=longest:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=9[a]" -map "[v]" -map "[a]" -t 30 -c:v libx264 -preset medium -crf 18 -c:a aac -b:a 192k -movflags +faststart $final 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Audio and subtitle composition failed." }
}
finally {
    Pop-Location
}

Write-Output $final
