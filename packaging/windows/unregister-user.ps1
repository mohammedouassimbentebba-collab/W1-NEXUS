$ErrorActionPreference = "Stop"
$roots = @(
  "HKCU:\Software\Classes\w1",
  "HKCU:\Software\Classes\.w1nexus",
  "HKCU:\Software\Classes\W1Nexus.Project"
)
foreach ($root in $roots) { if (Test-Path $root) { Remove-Item -Recurse -Force $root } }
