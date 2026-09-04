; XMacro-peater branded installer (Inno Setup 6)
; Build:  ISCC.exe /DAppVersion=1.0.0 installer\xmacro.iss
; Expects dist\XMacro-peater.exe (PyInstaller output) and
; installer\ViGEmBusSetup_x64.msi (staged by the release workflow).

#ifndef AppVersion
#define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B7E9C1D4-4F2A-4E8B-9A63-2D4F8C1A7E55}
AppName=XMacro-peater
AppVersion={#AppVersion}
AppPublisher=Xanonz
AppPublisherURL=https://github.com/xalaad/xmacro-peater
AppSupportURL=https://github.com/xalaad/xmacro-peater/issues
AppCopyright=(c) 2026 Xanonz - MIT License
VersionInfoCompany=Xanonz
VersionInfoDescription=XMacro-peater Setup
VersionInfoProductName=XMacro-peater
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\XMacro-peater
DefaultGroupName=XMacro-peater
UninstallDisplayIcon={app}\XMacro-peater.exe
OutputDir=Output
OutputBaseFilename=XMacro-peater-Setup-v{#AppVersion}
SetupIconFile=..\assets\xmacro.ico
WizardStyle=modern
WizardImageFile=wizard_large.bmp
WizardSmallImageFile=wizard_small.bmp
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableWelcomePage=no

[Dirs]
; User data lives next to the exe for easy access/editing/sharing —
; grant normal users write permission inside Program Files
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\config\schemes"; Permissions: users-modify
Name: "{app}\recordings"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\XMacro-peater\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "ViGEmBusSetup_x64.msi"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\XMacro-peater"; Filename: "{app}\XMacro-peater.exe"
Name: "{group}\Uninstall XMacro-peater"; Filename: "{uninstallexe}"
Name: "{autodesktop}\XMacro-peater"; Filename: "{app}\XMacro-peater.exe"; \
    Tasks: desktopicon

[Run]
; Silent one-time ViGEmBus driver install — skipped when already present
Filename: "msiexec.exe"; \
    Parameters: "/i ""{tmp}\ViGEmBusSetup_x64.msi"" /qn /norestart"; \
    StatusMsg: "Installing the ViGEmBus virtual controller driver..."; \
    Check: not VigemInstalled
Filename: "{app}\XMacro-peater.exe"; Description: "Launch XMacro-peater"; \
    Flags: nowait postinstall skipifsilent

[Code]
function VigemInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\ViGEmBus');
end;
