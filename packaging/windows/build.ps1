$ErrorActionPreference = "Stop"
python -m pip install --upgrade build pyinstaller
python -m pip install ".[desktop,office]"
pyinstaller --noconfirm packaging/windows/w1-nexus.spec
$inno = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $inno) {
  $candidate = Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"
  $candidateX86 = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
  if (Test-Path $candidate) { $inno = $candidate } elseif (Test-Path $candidateX86) { $inno = $candidateX86 } else { throw "Inno Setup 6 (ISCC.exe) is required." }
}
$exe = "dist\W1 Nexus\W1 Nexus.exe"
if ($env:W1_WINDOWS_SIGN_CERT_SHA1) {
  & signtool sign /sha1 $env:W1_WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $exe
  if ($LASTEXITCODE -ne 0) { throw "Executable Authenticode signing failed." }
} else {
  Write-Warning "Executable remains unsigned: W1_WINDOWS_SIGN_CERT_SHA1 is not configured."
}
& $inno packaging/windows/installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
$installer = "packaging\windows\out\W1-Nexus-0.1.0-dev50-windows-x64.exe"
if ($env:W1_WINDOWS_SIGN_CERT_SHA1) {
  & signtool sign /sha1 $env:W1_WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $installer
  if ($LASTEXITCODE -ne 0) { throw "Installer Authenticode signing failed." }
  $exeStatus = (Get-AuthenticodeSignature -LiteralPath $exe).Status
  $installerStatus = (Get-AuthenticodeSignature -LiteralPath $installer).Status
  if ($exeStatus -ne "Valid" -or $installerStatus -ne "Valid") { throw "Authenticode verification failed after signing." }
} else {
  Write-Warning "Installer remains unsigned and MUST NOT be published as a trusted release."
}
Write-Host "Built W1 Nexus 0.1.0-dev50; publication still requires a verified signing identity."
