; Inno Setup 6 script for On-Screen Keyboard.
; Build (after PyInstaller produced ..\dist\OnScreenKeyboard\):
;   iscc /DAppVersion=0.1.9 installer\OnScreenKeyboard.iss
; Output: installer\Output\OnScreenKeyboard-Setup-<AppVersion>.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "On-Screen Keyboard"
#define AppExe  "OnScreenKeyboard.exe"

[Setup]
; Fixed GUID: identifies the app across versions so upgrades replace in place.
AppId={{3E8A2B9F-3615-475A-A4EE-94978BD1DA6E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Gitties67
AppPublisherURL=https://github.com/Gitties67/Onscreen-keyboard
AppSupportURL=https://github.com/Gitties67/Onscreen-keyboard/issues
VersionInfoVersion={#AppVersion}
; Per-user by default (no admin / UAC); the dialog lets the user choose all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\OnScreenKeyboard
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
; Relaunch after silent self-update is done explicitly by [Run] below.
RestartApplications=no
SetupIconFile=..\osk.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=Output
OutputBaseFilename=OnScreenKeyboard-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\OnScreenKeyboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; Debug launch (writes %LOCALAPPDATA%\OnScreenKeyboard\osk_debug.log).  The app is single-instance:
; QUIT the running keyboard first, otherwise this just re-shows it and logs nothing new.
Name: "{autoprograms}\{#AppName} (debug mode)"; Filename: "{app}\{#AppExe}"; Parameters: "--debug"

[Registry]
; Per-user autostart; removed again on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OnScreenKeyboard"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
; Interactive installs: "Launch" checkbox on the final page.
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; Silent self-update (the app passes /RELAUNCH=1): start the new version.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

[UninstallDelete]
; Remove anything left in the install folder (e.g. files created at runtime).
; User settings in %APPDATA%\OnScreenKeyboard are intentionally kept.
Type: filesandordirs; Name: "{app}"

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
