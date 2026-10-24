; NexHub Windows installer (Inno Setup 6)
; Build after PyInstaller onedir:
;   python -m PyInstaller --onedir ... main.py
;   ISCC.exe /DMyAppVersion=0.1.6-beta NexHub.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "NexHub"
#define MyAppPublisher "NexHub"
#define MyAppExeName "NexHub.exe"
#define MyAppId "{{6F2B7F43-1C91-4D2C-9B7A-8E6F45A0C2D1}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\NexHub
DefaultGroupName=NexHub
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=NexHub-Setup-{#MyAppVersion}
SetupIconFile=..\nexhub\resources\nexhub.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
VersionInfoProductName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\NexHub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NexHub"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\NexHub"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch NexHub"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v NexHub /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
