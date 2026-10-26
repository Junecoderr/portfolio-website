; Inno Setup 6 script for TB on Windows -> tb-windows-x64-setup.exe
;
; Built by packaging/build.py on Windows (CI: windows-latest), from the
; PyInstaller one-folder build in dist\tb\. By hand, from code\:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=3.0.0 ^
;       /DSourceDir=dist\tb /DRepoDir=. /DOutputDir=dist packaging\windows\tb.iss
;
; What it does (per-user by default, no admin rights needed):
;   - installs dist\tb\ (tb.exe, tbw.exe = no console window for the GUI apps,
;     tblaunch.exe / tblaunchw.exe = the launchers `tb build` copies next to
;     a bundled app, _internal\, apps\, examples\, welcome.tb) and tb.ico into
;     %LOCALAPPDATA%\Programs\TB
;   - adds that folder to the user's PATH (removed again on uninstall)
;   - .tb files: TB icon (tb.ico, made from the site's favicon by
;     packaging/windows/make_icon.py); double-click runs them in a console
;     (TB waits for Enter before the window closes); right-click > Edit opens
;     Notepad
;   - Start menu folder "TB": TB Prompt, TB website, TB Examples, the GUI apps
; Files TB programs create later (for example the Care journal and the to-do
; list, saved next to their app) are left in place on uninstall.

#ifndef AppVersion
  #define AppVersion "3.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\tb"
#endif
#ifndef RepoDir
  #define RepoDir "..\.."
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define AppName "TB"
#define AppUrl "https://code.tanisha.ceo"

[Setup]
; Fixed forever: identifies TB for upgrades and uninstall. Never change it.
AppId={{5F5E3C55-FA16-4258-A72E-3F5BE66D4DF4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TB
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppComments=TB, a small, friendly programming language.
VersionInfoVersion={#AppVersion}
VersionInfoCompany=TB
VersionInfoProductName=TB
VersionInfoDescription=TB {#AppVersion} setup
; Per-user install: {autopf} is %LOCALAPPDATA%\Programs when not elevated.
; /ALLUSERS on the command line installs for everyone (needs admin).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
DefaultDirName={autopf}\TB
DefaultGroupName=TB
DisableProgramGroupPage=yes
DisableDirPage=auto
DisableReadyPage=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
ChangesAssociations=yes
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\tb.exe
OutputDir={#OutputDir}
OutputBaseFilename=tb-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#RepoDir}\packaging\windows\tb.ico
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
FinishedLabel=TB is installed.%n%nOpen "TB Prompt" from the Start menu and type:%n%n    tb welcome.tb%n%nor double-click any .tb file. The apps are in the Start menu too, in the TB folder.

[Files]
; dist\tb\ already holds tb.exe, tbw.exe, _internal\, apps\, examples\ and
; welcome.tb (build.py copies them in), the same as the portable zip.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "todo.json,journal.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoDir}\packaging\windows\tb.ico"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; .tb file type. HKA = the current user's classes for a per-user install.
Root: HKA; Subkey: "Software\Classes\.tb"; ValueType: string; ValueName: ""; ValueData: "TB.Program"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.tb\OpenWithProgids"; ValueType: string; ValueName: "TB.Program"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\TB.Program"; ValueType: string; ValueName: ""; ValueData: "TB program"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\TB.Program\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\tb.ico"",0"
Root: HKA; Subkey: "Software\Classes\TB.Program\shell"; ValueType: string; ValueName: ""; ValueData: "open"
Root: HKA; Subkey: "Software\Classes\TB.Program\shell\open"; ValueType: string; ValueName: ""; ValueData: "Run with TB"
Root: HKA; Subkey: "Software\Classes\TB.Program\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\tb.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\TB.Program\shell\edit"; ValueType: string; ValueName: ""; ValueData: "Edit"
Root: HKA; Subkey: "Software\Classes\TB.Program\shell\edit\command"; ValueType: string; ValueName: ""; ValueData: "notepad.exe ""%1"""

[INI]
Filename: "{group}\TB website.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#AppUrl}"

[Icons]
Name: "{group}\TB Prompt"; Filename: "{cmd}"; Parameters: "/k ""set ""PATH={app};%PATH%"" && tb --version && echo Type  tb FILE.tb  to run a program,  tb  to try TB line by line,  tb doc  to see the libraries."""; WorkingDir: "{userdocs}"; Comment: "A command prompt where you can type tb"
Name: "{group}\TB Examples"; Filename: "{app}"; Comment: "The folder with the example programs and apps"
Name: "{group}\Care check-in"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\care\main.tb"""; WorkingDir: "{app}\apps\care"; IconFilename: "{app}\tb.exe"
Name: "{group}\To-do"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\todo\main.tb"""; WorkingDir: "{app}\apps\todo"; IconFilename: "{app}\tb.exe"
Name: "{group}\Notes"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\notes\main.tb"""; WorkingDir: "{app}\apps\notes"; IconFilename: "{app}\tb.exe"
Name: "{group}\Calculator"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\calculator\main.tb"""; WorkingDir: "{app}\apps\calculator"; IconFilename: "{app}\tb.exe"
Name: "{group}\Hash checker"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\hash-checker\main.tb"""; WorkingDir: "{app}\apps\hash-checker"; IconFilename: "{app}\tb.exe"
Name: "{group}\Log triage"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\log-triage-gui\main.tb"""; WorkingDir: "{app}\apps\log-triage-gui"; IconFilename: "{app}\tb.exe"
Name: "{group}\IOC workbench"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\ioc-workbench\main.tb"""; WorkingDir: "{app}\apps\ioc-workbench"; IconFilename: "{app}\tb.exe"
Name: "{group}\Alerts dashboard"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\alerts-dashboard-gui\main.tb"""; WorkingDir: "{app}\apps\alerts-dashboard-gui"; IconFilename: "{app}\tb.exe"
Name: "{group}\Network monitor"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\network-monitor\main.tb"""; WorkingDir: "{app}\apps\network-monitor"; IconFilename: "{app}\tb.exe"
Name: "{group}\File inspector"; Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\file-inspector-gui\main.tb"""; WorkingDir: "{app}\apps\file-inspector-gui"; IconFilename: "{app}\tb.exe"
Name: "{group}\Uninstall TB"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\tbw.exe"; Parameters: """{app}\apps\care\main.tb"""; WorkingDir: "{app}\apps\care"; Description: "Open the Care check-in app"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: files; Name: "{group}\TB website.url"

[Code]
const
  UserEnvKey = 'Environment';
  SystemEnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

function EnvRoot: Integer;
begin
  if IsAdminInstallMode then
    Result := HKEY_LOCAL_MACHINE
  else
    Result := HKEY_CURRENT_USER;
end;

function EnvKey: String;
begin
  if IsAdminInstallMode then
    Result := SystemEnvKey
  else
    Result := UserEnvKey;
end;

function SamePath(A, B: String): Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(Trim(A)), RemoveBackslashUnlessRoot(Trim(B))) = 0;
end;

{ Split Paths on ';' and drop every entry equal to Dir. Found is set when Dir was there. }
function WithoutDir(Paths, Dir: String; var Found: Boolean): String;
var
  Rest, Item: String;
  P: Integer;
begin
  Result := '';
  Found := False;
  Rest := Paths;
  while Rest <> '' do
  begin
    P := Pos(';', Rest);
    if P = 0 then
    begin
      Item := Rest;
      Rest := '';
    end
    else
    begin
      Item := Copy(Rest, 1, P - 1);
      Rest := Copy(Rest, P + 1, Length(Rest));
    end;
    if Trim(Item) = '' then
      continue;
    if SamePath(Item, Dir) then
      Found := True
    else
    begin
      if Result <> '' then
        Result := Result + ';';
      Result := Result + Item;
    end;
  end;
end;

procedure AddToPath(Dir: String);
var
  Paths, Kept: String;
  Found: Boolean;
begin
  if not RegQueryStringValue(EnvRoot, EnvKey, 'Path', Paths) then
    Paths := '';
  Kept := WithoutDir(Paths, Dir, Found);
  if Found then
    exit;
  if Kept <> '' then
    Kept := Kept + ';';
  if not RegWriteExpandStringValue(EnvRoot, EnvKey, 'Path', Kept + Dir) then
    Log('Could not add ' + Dir + ' to PATH');
end;

procedure RemoveFromPath(Dir: String);
var
  Paths, Kept: String;
  Found: Boolean;
begin
  if not RegQueryStringValue(EnvRoot, EnvKey, 'Path', Paths) then
    exit;
  Kept := WithoutDir(Paths, Dir, Found);
  if not Found then
    exit;
  if Kept = '' then
    RegDeleteValue(EnvRoot, EnvKey, 'Path')
  else
    RegWriteExpandStringValue(EnvRoot, EnvKey, 'Path', Kept);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddToPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
