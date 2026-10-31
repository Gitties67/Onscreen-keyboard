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
; CI also writes osk-signing.iss: #define CertThumb "<SHA-1 thumbprint>".
#if FileExists(AddBackslash(SourcePath) + "osk-signing.cer") && \
    FileExists(AddBackslash(SourcePath) + "osk-signing.iss")
  #include "osk-signing.iss"
  #define SignedTop
#endif

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
; Older versions installed per-user.  Don't reuse that mode on update: install
; for all users and remove the old per-user copy (PrepareToInstall below).
UsePreviousPrivileges=no
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
Source: "osk-signing.cer"; Flags: dontcopy
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
; Interactive installs: "Launch" checkbox on the final page.  shellexec: the
; uiAccess exe cannot be started with CreateProcess (ERROR_ELEVATION_REQUIRED).
Filename: "{app}\{code:AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser shellexec
; Silent self-update (the app passes /RELAUNCH=1): start the new version.
Filename: "{app}\{code:AppExeName}"; Flags: nowait runasoriginaluser shellexec; Check: RelaunchRequested


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

// Pre-0.1.28 versions installed per-user (%LOCALAPPDATA%\Programs).  An
// update now installs to Program Files, so silently uninstall that old copy
// first: otherwise its shortcuts, autostart entry and exe stay behind and two
// keyboards fight over the single-instance lock.  Settings (%APPDATA%) stay.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Uninst: String;
  Code, I: Integer;
begin
  Result := '';
  if not IsAdminInstallMode then
    exit;
  if not RegQueryStringValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{3E8A2B9F-3615-475A-A4EE-94978BD1DA6E}_is1',
      'UninstallString', Uninst) then
    exit;
  Uninst := RemoveQuotes(Uninst);
  if not FileExists(Uninst) then
    exit;
  Log('Removing old per-user install: ' + Uninst);
  if Exec(Uninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE,
          ewWaitUntilTerminated, Code) then
    // unins000.exe hands over to a temp copy and returns at once: wait for
    // the real uninstall to finish (it deletes unins000.exe last).
    for I := 1 to 60 do
    begin
      if not FileExists(Uninst) then
        break;
      Sleep(500);
    end;
end;

#ifdef SignedTop
// The signed uiAccess exe only starts if Windows trusts its certificate.  Each
// build has its own (private key deleted after signing).  Trust it in the
// machine Root store with certutil, remembering its thumbprint in HKLM so an
// update or uninstall removes exactly that certificate.  certutil's output
// goes into the setup log so a failure says why.
const
  CertKey = 'Software\Gitties67\OnScreenKeyboard';

function RunLogged(Params: String): Integer;
var
  OutFile: String;
  Output: AnsiString;
begin
  OutFile := ExpandConstant('{tmp}\certutil-out.txt');
  if not Exec(ExpandConstant('{cmd}'),
              '/C ""' + ExpandConstant('{sys}\certutil.exe') + '" ' + Params +
              ' > "' + OutFile + '" 2>&1"', '', SW_HIDE, ewWaitUntilTerminated, Result) then
    Result := -1;
  Log('certutil ' + Params + ' -> exit ' + IntToStr(Result));
  if LoadStringFromFile(OutFile, Output) then
    Log(String(Output));
end;

procedure RemoveTrustedCert;
var
  Old: String;
begin
  if RegQueryStringValue(HKLM, CertKey, 'CertThumb', Old) and (Old <> '') then
  begin
    RunLogged('-delstore Root ' + Old);
    RegDeleteValue(HKLM, CertKey, 'CertThumb');
    RegDeleteKeyIfEmpty(HKLM, CertKey);
  end;
end;

procedure TrustSigningCert;
begin
  RemoveTrustedCert;   // previous version's certificate
  ExtractTemporaryFile('osk-signing.cer');
  if RunLogged('-f -addstore Root "' + ExpandConstant('{tmp}\osk-signing.cer') + '"') = 0 then
    RegWriteStringValue(HKLM, CertKey, 'CertThumb', '{#CertThumb}')
  else
    Log('ERROR: could not trust the signing certificate; the keyboard will not stay above Start.');
end;
#endif

procedure CurStepChanged(CurStep: TSetupStep);
begin
#ifdef SignedTop
  // ssInstall (before files are copied): the certificate must be trusted
  // before the [Run] entries start the signed exe.
  if (CurStep = ssInstall) and IsAdminInstallMode then
    TrustSigningCert;
#endif
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
#ifdef SignedTop
  if (CurUninstallStep = usPostUninstall) and IsAdminInstallMode then
    RemoveTrustedCert;
#endif
end;

function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
