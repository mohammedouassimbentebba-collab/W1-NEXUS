$ErrorActionPreference = "Stop"
$exe = Join-Path $PSScriptRoot "W1 Nexus.exe"
if (-not (Test-Path $exe)) { throw "W1 executable not found: $exe" }
$protocol = "HKCU:\Software\Classes\w1"
New-Item -Force $protocol | Out-Null
Set-ItemProperty $protocol "(default)" "URL:W1 Nexus Protocol"
Set-ItemProperty $protocol "URL Protocol" ""
New-Item -Force "$protocol\shell\open\command" | Out-Null
Set-ItemProperty "$protocol\shell\open\command" "(default)" ('"' + $exe + '" --deep-link "%1"')
$ext = "HKCU:\Software\Classes\.w1nexus"
New-Item -Force $ext | Out-Null
Set-ItemProperty $ext "(default)" "W1Nexus.Project"
$project = "HKCU:\Software\Classes\W1Nexus.Project"
New-Item -Force "$project\shell\open\command" | Out-Null
Set-ItemProperty "$project\shell\open\command" "(default)" ('"' + $exe + '" --project "%1"')
