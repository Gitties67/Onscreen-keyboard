; Inno Setup 6 script for On-Screen Keyboard.
; Build (after PyInstaller produced ..\dist\OnScreenKeyboard\):
;   iscc /DAppVersion=0.1.9 installer\OnScreenKeyboard.iss
; Output: installer\Output\OnScreenKeyboard-Setup-<AppVersion>.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "On-Screen Keyboard"
#define AppExe  "OnScreenKeyboard.exe"
; CI drops the public half of the per-build signing certificate here (its
; private key is deleted once OnScreenKeyboardTop.exe is signed).  Without it
; (local builds) every install mode just uses the plain exe.
#if FileExists(AddBackslash(SourcePath) + "osk-signing.cer")
  #define SignedTop
#endif
; Marker in the certificate subject, used to find our certificates again.
#define CertMarker "OU=OnScreenKeyboard-uiaccess"

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
; Always an admin install in Program Files: only there can the signed
; uiAccess build stay above Start / Search.  No "all users / only for me"
; question; /CURRENTUSER on the command line still installs per-user (CI's
; smoke test, and self-updates of older per-user installs).
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
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

[InstallDelete]
; Upgrades install over the old version (same AppId, same folder).  Clear the
; PyInstaller bundle first so files a newer build no longer ships don't pile up.
; Settings live in %APPDATA%\OnScreenKeyboard and are never touched.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\OnScreenKeyboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef SignedTop
Source: "osk-signing.cer"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: IsAdminInstallMode
#endif

[Icons]
; AppUserModelID must match APP_USER_MODEL_ID in keyboard.py so a pinned taskbar
; icon and the running keyboard share one taskbar button.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{code:AppExeName}"; AppUserModelID: "Gitties67.OnScreenKeyboard"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{code:AppExeName}"; Tasks: desktopicon; AppUserModelID: "Gitties67.OnScreenKeyboard"
; Debug launch (writes %LOCALAPPDATA%\OnScreenKeyboard\osk_debug.log).  The app is single-instance:
; QUIT the running keyboard first, otherwise this just re-shows it and logs nothing new.
; No AppUserModelID here on purpose: pinning must resolve to the normal shortcut.
Name: "{autoprograms}\{#AppName} (debug mode)"; Filename: "{app}\{code:AppExeName}"; Parameters: "--debug"

[Registry]
; Autostart (HKCU for "only for me", HKLM for all users); removed on uninstall.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OnScreenKeyboard"; ValueData: """{app}\{code:AppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
#ifdef SignedTop
; Trust this build's signing certificate (machine Trusted Root) so Windows will
; start the uiAccess exe, replacing any older build's certificate.  It is
; limited to code signing and its private key no longer exists anywhere.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-ChildItem Cert:\LocalMachine\Root | Where-Object {{ $_.Subject -like '*{#CertMarker}*' }} | Remove-Item; Import-Certificate -FilePath '{tmp}\osk-signing.cer' -CertStoreLocation Cert:\LocalMachine\Root | Out-Null"""; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode; StatusMsg: "Trusting the keyboard's signature..."
#endif
; Interactive installs: "Launch" checkbox on the final page.  shellexec: the
; uiAccess exe cannot be started with CreateProcess (ERROR_ELEVATION_REQUIRED).
Filename: "{app}\{code:AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser shellexec
; Silent self-update (the app passes /RELAUNCH=1): start the new version.
Filename: "{app}\{code:AppExeName}"; Flags: nowait runasoriginaluser shellexec; Check: RelaunchRequested

#ifdef SignedTop
[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-ChildItem Cert:\LocalMachine\Root | Where-Object {{ $_.Subject -like '*{#CertMarker}*' }} | Remove-Item"""; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode; RunOnceId: "RemoveSigningCert"
#endif

[UninstallDelete]
; Remove anything left in the install folder (e.g. files created at runtime).
; User settings in %APPDATA%\OnScreenKeyboard are intentionally kept.
Type: filesandordirs; Name: "{app}"

[Code]
function AppExeName(Param: String): String;
begin
  Result := '{#AppExe}';
#ifdef SignedTop
  if IsAdminInstallMode then
    Result := 'OnScreenKeyboardTop.exe';
#endif
end;

function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
