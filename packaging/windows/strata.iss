; Inno Setup script for Strata (Windows 10 / 11).
;
; Produces: an installer, a Start-menu entry, an optional desktop shortcut, an
; uninstaller, the application icon, and a `.strata` workspace file association.
;
; Signing: set SignTool in the Inno Setup IDE, or sign the output afterwards.
; Releases must be signed — an unsigned installer trains users to click through
; SmartScreen, which is a security regression, not a papercut.

#define AppName "Strata"
#define AppVersion "1.3.1"
#define AppPublisher "Strata"
#define AppExeName "Strata.exe"

[Setup]
AppId={{8E2C4E38-2B9E-4E3A-9E1C-2E6C1B7A9D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=..\..\dist\installer
OutputBaseFilename=Strata-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=..\icons\strata.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\Strata\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.strata"; ValueType: string; ValueName: ""; ValueData: "Strata.Workspace"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Strata.Workspace"; ValueType: string; ValueName: ""; ValueData: "Strata Workspace"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Strata.Workspace\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKA; Subkey: "Software\Classes\Strata.Workspace\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The user's workspaces and settings are theirs. Uninstalling removes the
; application, never the knowledge — deleting someone's notes because they
; uninstalled an app would be indefensible.
Type: files; Name: "{app}\*.log"
