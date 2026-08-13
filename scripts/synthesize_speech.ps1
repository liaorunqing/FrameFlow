param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutputFile,
    [string]$Voice = "Microsoft Huihui Desktop",
    [int]$Rate = 0,
    [int]$Volume = 100
)

$ErrorActionPreference = "Stop"
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8).Trim()
$synth = New-Object -ComObject SAPI.SpVoice
$stream = New-Object -ComObject SAPI.SpFileStream
try {
    $available = @($synth.GetVoices())
    $match = $available | Where-Object { $_.GetDescription() -eq $Voice } | Select-Object -First 1
    if ($null -ne $match) {
        $synth.Voice = $match
    }
    else {
        Write-Warning "Voice '$Voice' is unavailable; using the system default voice."
    }
    $synth.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate))
    $synth.Volume = [Math]::Max(0, [Math]::Min(100, $Volume))
    $stream.Open($OutputFile, 3, $false)
    $synth.AudioOutputStream = $stream
    [void]$synth.Speak($text)
}
finally {
    $stream.Close()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($stream)
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($synth)
}
