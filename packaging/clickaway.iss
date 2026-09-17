; Windows installer for ClickAway. Built by scripts/build.py:
;   ISCC.exe /DMyAppVersion=0.3.0 packaging/clickaway.iss
; Installs per user by default, so no administrator prompt is needed.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "ClickAway"
#define MyAppExeName "ClickAway.exe"
#define MyAppPublisher "ClickAway contributors"
#define MyAppURL "https://github.com/ElMariones/ClickAway"

[Setup]
AppId={{7F4A1C2E-9B3D-4E85-A6F1-2C8D5B0E9A37}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename=ClickAway-Setup-x64
SetupIconFile=..\clickaway\assets\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\ClickAway\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=Setup has finished installing [name] on your computer.%n%nThe first time you start sharing, Windows Firewall will ask for access. Choose Allow so your Mac can connect.
FinishedLabelNoIcons=Setup has finished installing [name] on your computer.%n%nThe first time you start sharing, Windows Firewall will ask for access. Choose Allow so your Mac can connect.
