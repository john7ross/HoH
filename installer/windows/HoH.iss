#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={{8F6659AB-3141-4976-9DF5-26B8A983A23A}
AppName=HoH
AppVersion={#AppVersion}
AppVerName=HoH {#AppVersion}
AppPublisher=HoH
DefaultDirName={localappdata}\Programs\HoH
DefaultGroupName=HoH
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableWelcomePage=no
Compression=lzma2/ultra64
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename=HoH-Setup-{#AppVersion}
UninstallDisplayName=HoH {#AppVersion}
UninstallDisplayIcon={app}\hoh.ico
VersionInfoVersion={#AppVersion}
VersionInfoProductName=HoH
VersionInfoDescription=HoH agent orchestration desktop application
VersionInfoCompany=HoH
SetupLogging=yes
CloseApplications=force
RestartApplications=no
#ifdef AppIcon
SetupIconFile={#AppIcon}
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\HoH"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\launch-hoh-gui.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\hoh.ico"
Name: "{autodesktop}\HoH"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\launch-hoh-gui.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\hoh.ico"; Tasks: desktopicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\launch-hoh-gui.ps1"""; Description: "Launch HoH"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
