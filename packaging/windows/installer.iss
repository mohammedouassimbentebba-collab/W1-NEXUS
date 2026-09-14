#define MyAppName "W1 Nexus"
#define MyAppVersion "0.1.0-dev50"
#define MyAppPublisher "W1 Nexus Project"
#define MyAppExeName "W1 Nexus.exe"

[Setup]
AppId={{9BDEF418-CE17-5B6D-B35C-AD60CFEB5B2F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=W1 Nexus AI orchestration platform
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\W1 Nexus
DefaultGroupName=W1 Nexus
OutputDir=out
OutputBaseFilename=W1-Nexus-0.1.0-dev50-windows-x64
SetupIconFile=..\..\brand\production\w1-nexus.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\..\dist\W1 Nexus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\W1 Nexus"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\Classes\w1"; ValueType: string; ValueName: ""; ValueData: "URL:W1 Nexus Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\w1"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\w1\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --deep-link ""%1"""
Root: HKCU; Subkey: "Software\Classes\.w1nexus"; ValueType: string; ValueName: ""; ValueData: "W1Nexus.Project"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\W1Nexus.Project"; ValueType: string; ValueName: ""; ValueData: "W1 Nexus Project"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\W1Nexus.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --project ""%1"""
