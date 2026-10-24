; installer/baihe.iss -- Baihe Studio's Windows installer (Step 80b).
; Design: docs/windows-installer-design.md. Build it with
; installer/build_installer.py, which assembles the payload and passes the
; /D defines below to ISCC (Inno Setup 6).
;
; What goes where:
;   {app}\python\   the bundled embeddable Python (plus whatever pip installs)
;   {app}\app\      the app's code and the prebuilt React app (frontend\dist)
;   data folder     library, .env (settings and API keys), model_cache --
;                   per user, chosen on the "Where to keep your library" page,
;                   default %LOCALAPPDATA%\Baihe Studio, and never part of the
;                   payload. The install step writes it into {app}\app\INSTALLED.
;
; Per-user install (no admin rights): {autopf} is %LOCALAPPDATA%\Programs here,
; so Diagnostics' Install buttons can add optional packages to {app}\python.
;
; Boot service (the "service" task, on by default): Setup asks for
; administrator permission once, after the files are copied, and
; {app}\service\helper\lib\installer\service.py creates the BaiheStudio
; service (python -m api on 127.0.0.1 and nothing else, started with
; Windows), on the default port or the user's BAIHE_API_PORT if set (a
; fresh install only: an update keeps the service's stored port). Once the
; service exists, the launcher uses the service's stored port, and the
; Start-menu item "Baihe Studio service" changes it (service.py set-port).
; An update stops it first,
; and uninstalling removes it. Unticking the task (or /MERGETASKS="!service")
; keeps the Start-menu launcher only.
;
; Silent install (CI, power users):
;   BaiheStudio-Setup-<v>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="..." /DATADIR="..."
; Setup exits with code 100 if the files were copied but the Python packages
; didn't install (details in <data>\launcher\install.log), and 101 if the
; background service couldn't be set up (%ProgramFiles%\Baihe Studio Services\service.log).
; Silent uninstall ({app}\unins000.exe /VERYSILENT) keeps all user data; add /CLEAN
; to remove everything Baihe Studio made (clean uninstall).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.1.0.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\build\installer\payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\installer\output"
#endif
#ifndef ExtraDiskSpace
  #define ExtraDiskSpace "600000000"
#endif
#define AppName "Baihe Studio"

[Setup]
AppId={{973BDBB4-4ADC-4E54-973B-682E2A04362F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersionNumeric}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=BaiheStudio-Setup-{#AppVersion}
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\app\assets\app_icon.ico
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; pip unpacks the bundled wheels after the file copy, so Inno's own
; free-space check has to count them (build_installer.py measures it).
ExtraDiskSpaceRequired={#ExtraDiskSpace}
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
; One Setup at a time: a second click on "Install" in the app (or a second
; double-click) finds this mutex and Setup says it is already running.
SetupMutex=BaiheStudioSetupMutex,Global\BaiheStudioSetupMutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "service"; Description: "Run Baihe Studio in the &background from startup (a Windows service; Setup asks for administrator permission)"; GroupDescription: "Startup:"

[InstallDelete]
; An upgrade replaces the code wholesale (so a deleted module can't linger)
; but keeps {app}\python, where optional packages added through Diagnostics live.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\service"
; The app-only update's journal (installer/app_swap.py): a full install
; replaces whatever state it describes. The kept app-<version> folders go in
; CurStepChanged, by name and only from a Baihe Studio install folder.
Type: filesandordirs; Name: "{app}\app-update"
; The kept core wheels are this Setup's, never an earlier one's: a restore
; picks them by the versions this Setup installs.
Type: filesandordirs; Name: "{app}\wheels"

[Registry]
; The sign-in recovery an interrupted app-only update registers; a full
; install or an uninstall leaves nothing for it to recover.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\RunOnce"; ValueName: "!BaiheStudioAppUpdate"; ValueType: none; Flags: deletevalue uninsdeletevalue

[Files]
Source: "{#PayloadDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\service\*"; DestDir: "{app}\service"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\manifest.json"; DestDir: "{app}"; Flags: ignoreversion
; Kept after the install: a Diagnostics install that fails puts these core
; packages back from here when the network is down (pending_install.restore_commands).
Source: "{#PayloadDir}\wheels\*"; DestDir: "{app}\wheels"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\app_icon.ico"; Comment: "Start Baihe Studio and open it in its own window"
Name: "{group}\Stop {#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"" --stop"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\app_icon.ico"; Comment: "Stop Baihe Studio's server"
; The service menu (installer/service_menu.ps1) asks for administrator
; rights, so the shortcut runs the admin-only folder's copy, never {app}'s.
Name: "{group}\{#AppName} service"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{commonpf64}\Baihe Studio Services\helper\lib\installer\service_menu.ps1"""; WorkingDir: "{sys}"; IconFilename: "{app}\app\assets\app_icon.ico"; Comment: "Baihe Studio's background service: status, ports, remote access, logs"; Tasks: service
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"""; WorkingDir: "{app}\app"; Description: "Start Baihe Studio now"; Flags: postinstall nowait skipifsilent; Check: PackagesInstalled

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"" --stop"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "StopServer"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  PostInstallFailed, ServiceFailed: Boolean;
  DataDirCreatedBySetup, DataDirIsNew: Boolean;
  // Uninstall state, read in InitializeUninstall while the files still exist.
  UninstDataDir: String;
  UninstDataDirCreated: Boolean;
  ProgramOwned: Boolean;
  DeleteLibrary, DeleteSettings, DeleteModels, CleanAll: Boolean;
  LibraryBox, SettingsBox, ModelsBox, CleanBox: TNewCheckBox;
  PrevLibrary, PrevSettings, PrevModels: Boolean;

const
  CreatedFlagLine = '# created-by-setup';
  AppServiceName = 'BaiheStudio';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{973BDBB4-4ADC-4E54-973B-682E2A04362F}_is1';

function DefaultDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#AppName}');
end;

function NormDir(const Dir: String): String;
begin
  Result := Lowercase(AddBackslash(RemoveBackslashUnlessRoot(Trim(Dir))));
end;

function IsInsideOrSame(const Path, Dir: String): Boolean;
begin
  Result := (Dir <> '') and (Pos(NormDir(Dir), NormDir(Path)) = 1);
end;

// '' if Dir is a usable data folder, otherwise why not. Mirrors
// postinstall.validate_data_dir: a full path, not a whole drive, and not
// inside the install folder (upgrades and uninstalling replace that).
function DataDirProblem(const Dir, AppDir: String): String;
var
  D: String;
begin
  Result := '';
  D := RemoveBackslashUnlessRoot(Trim(Dir));
  if not (((Length(D) >= 3) and (D[2] = ':') and (D[3] = '\')) or (Copy(D, 1, 2) = '\\')) then
    Result := 'Choose a full folder path for your data, for example ' + DefaultDataDir() + '.'
  else if (Length(D) = 3) or (NormDir(D) = NormDir(ExtractFileDrive(D))) then
    Result := 'Choose a folder for your data, not a whole drive.'
  else if IsInsideOrSame(D, AppDir) then
    Result := 'Your data folder can''t be inside the install folder (' + AppDir +
      '): updates and uninstalling replace that folder.';
end;

// True if AppDir is a Baihe Studio install: its manifest.json names the
// product and this installer's AppId (build_installer.write_manifest), or
// this app's own uninstall entry says it's installed there. A folder that
// merely has some other program's manifest.json doesn't count.
function IsBaiheInstallDir(const AppDir: String): Boolean;
var
  Manifest: AnsiString;
  Location: String;
begin
  Result := False;
  if LoadStringFromFile(AddBackslash(AppDir) + 'manifest.json', Manifest) then
    Result := (Pos('"product": "Baihe Studio"', Manifest) > 0) and
      (Pos('973BDBB4-4ADC-4E54-973B-682E2A04362F', Manifest) > 0);
  if (not Result) and RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', Location) then
    Result := NormDir(Location) = NormDir(AppDir);
end;

// '' unless the install folder already holds a python or app folder that
// Baihe Studio didn't put there: updating would overwrite it, and
// uninstalling would delete it.
function InstallDirProblem(const AppDir: String): String;
begin
  Result := '';
  if (DirExists(AddBackslash(AppDir) + 'python') or DirExists(AddBackslash(AppDir) + 'app'))
     and not IsBaiheInstallDir(AppDir) then
    Result := 'The folder ' + AppDir + ' already has a "python" or "app" folder that Baihe Studio ' +
      'didn''t install. Choose an empty folder, or a new one.';
end;

// True for the folder names installer/app_swap.py gives the versions it
// keeps and its unfinished unpacks: app-<n>.<n>[.<n>], an optional -/+ tail
// of letters, digits, '.', '+' and '-', and an optional '.partial'. Anything
// else that starts with "app-" may be the user's and is never deleted.
function IsAppVersionName(const FolderName: String): Boolean;
var
  S: String;
  I, Dots, Digits: Integer;
  C: Char;
begin
  Result := False;
  S := FolderName;
  if Copy(S, 1, 4) <> 'app-' then
    Exit;
  if (Length(S) > 12) and (Copy(S, Length(S) - 7, 8) = '.partial') then
    S := Copy(S, 1, Length(S) - 8);
  I := 5;
  Dots := 0;
  Digits := 0;
  while I <= Length(S) do
  begin
    C := S[I];
    if (C >= '0') and (C <= '9') then
      Digits := Digits + 1
    else if (C = '.') and (Digits > 0) and (Dots < 2) then
    begin
      Dots := Dots + 1;
      Digits := 0;
    end
    else
      Break;
    I := I + 1;
  end;
  if (Dots = 0) or (Digits = 0) then
    Exit;
  if I <= Length(S) then
  begin
    if ((S[I] <> '-') and (S[I] <> '+')) or (I = Length(S)) then
      Exit;
    while I <= Length(S) do
    begin
      C := S[I];
      if not (((C >= '0') and (C <= '9')) or ((C >= 'A') and (C <= 'Z')) or
              ((C >= 'a') and (C <= 'z')) or (C = '.') or (C = '+') or (C = '-')) then
        Exit;
      I := I + 1;
    end;
  end;
  Result := True;
end;

// Path's own attributes (a link's, not its target's). False when they
// can't be read.
function PathAttributes(const Path: String; var Attributes: DWord): Boolean;
var
  Rec: TFindRec;
begin
  Result := FindFirst(RemoveBackslashUnlessRoot(Path), Rec);
  if Result then
  try
    Attributes := Rec.Attributes;
  finally
    FindClose(Rec);
  end;
end;

// True when Path is a junction or a symbolic link (FILE_ATTRIBUTE_REPARSE_POINT).
function IsLink(const Path: String): Boolean;
var
  Attributes: DWord;
begin
  Result := PathAttributes(Path, Attributes) and ((Attributes and $400) <> 0);
end;

// Deletes Path and everything in it, removing every junction or symbolic
// link as a link only: following one could delete what it points to, outside
// the folder being removed. Inno's DelTree doesn't promise that for links
// nested inside, so it only ever deletes the files of one plain folder here.
function DelTreeNoLinks(const Path: String): Boolean;
var
  Rec: TFindRec;
  Child: String;
  Attributes: DWord;
begin
  // A folder whose own attributes can't be read might be a link: leave it,
  // and the caller reports it as not removed.
  Result := False;
  if not PathAttributes(Path, Attributes) then
    Exit;
  if (Attributes and $400) <> 0 then
  begin
    Result := RemoveDir(Path) or DeleteFile(Path);
    Exit;
  end;
  if FindFirst(AddBackslash(Path) + '*', Rec) then
  try
    repeat
      Child := AddBackslash(Path) + Rec.Name;
      if (Rec.Name <> '.') and (Rec.Name <> '..') and ((Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) then
      begin
        if (Rec.Attributes and $400) <> 0 then
          RemoveDir(Child)
        else
          DelTreeNoLinks(Child);
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
  Result := DelTree(Path, True, True, False);
end;

// Deletes the earlier app versions app-only updates kept directly in AppDir,
// matched by IsAppVersionName. A junction or symlink is removed as a link
// only. Returns how many.
function DeleteAppVersionFolders(const AppDir: String): Integer;
var
  Rec: TFindRec;
  Path: String;
begin
  Result := 0;
  if FindFirst(AddBackslash(AppDir) + 'app-*', Rec) then
  try
    repeat
      Path := AddBackslash(AppDir) + Rec.Name;
      if ((Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and IsAppVersionName(Rec.Name) then
      begin
        if (Rec.Attributes and $400) <> 0 then // FILE_ATTRIBUTE_REPARSE_POINT
        begin
          if RemoveDir(Path) then
            Result := Result + 1;
        end
        else if DelTreeNoLinks(Path) then
          Result := Result + 1;
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

// The data folder named in an INSTALLED marker (postinstall.py writes it,
// UTF-8 with a BOM, which LoadStringsFromFile reads correctly), and
// whether Setup created that folder. '' if there's no usable marker.
function ReadMarker(const MarkerPath, AppDir: String; var Created: Boolean): String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line: String;
begin
  Result := '';
  Created := False;
  if not LoadStringsFromFile(MarkerPath, Lines) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Trim(Lines[I]);
    if Line = CreatedFlagLine then
      Created := True
    else if (Result = '') and (Line <> '') and (Line[1] <> '#') then
      Result := RemoveBackslashUnlessRoot(Line);
  end;
  if (Result <> '') and (DataDirProblem(Result, AppDir) <> '') then
  begin
    Result := '';
    Created := False;
  end;
end;

function DataDir(): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(DataDirPage.Values[0]));
end;

// The Baihe Studio items already in Dir, by the names an uninstall that
// ticks "delete" removes (library, .env, model_cache), as a bullet list;
// '' if none.
function ExistingDataItems(const Dir: String): String;
begin
  Result := '';
  if DirExists(AddBackslash(Dir) + 'library') then
    Result := Result + #13#10 + '  - library (projects, backups)';
  if FileExists(AddBackslash(Dir) + '.env') then
    Result := Result + #13#10 + '  - .env (settings and API keys)';
  if DirExists(AddBackslash(Dir) + 'model_cache') then
    Result := Result + #13#10 + '  - model_cache (downloaded AI models)';
end;

procedure InitializeWizard();
var
  Initial: String;
begin
  DataDirPage := CreateInputDirPage(wpSelectDir,
    'Where to keep your library',
    'Your projects, settings and API keys, and downloaded AI models are kept in one folder, separate from the program.',
    'Baihe Studio keeps your data in the folder below. Updates never touch it, and uninstalling only deletes it if you tick the box for that.' + #13#10#13#10 +
    'API keys you enter in Settings are saved here, on this PC only.',
    False, '');
  DataDirPage.Add('');
  Initial := ExpandConstant('{param:DATADIR}');
  if Initial = '' then
    Initial := GetPreviousData('DataDir', DefaultDataDir());
  DataDirPage.Values[0] := Initial;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDir', DataDir());
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem, OldDir, Existing: String;
  OldCreated: Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Problem := InstallDirProblem(WizardDirValue());
    if Problem <> '' then
    begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = DataDirPage.ID then
  begin
    Problem := DataDirProblem(DataDir(), WizardDirValue());
    if Problem <> '' then
    begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end
    // An existing folder outside the user's own profile keeps whatever
    // permissions it has, which may let other accounts on this PC read
    // the API keys saved there. (A new folder there is limited to this
    // account by the install step.) Not asked again when an update keeps
    // the folder the previous install already used.
    else
    begin
      OldDir := ReadMarker(AddBackslash(WizardDirValue()) + 'app\INSTALLED', WizardDirValue(), OldCreated);
      if DirExists(DataDir()) and (NormDir(OldDir) <> NormDir(DataDir()))
         and not IsInsideOrSame(DataDir(), ExpandConstant('{%USERPROFILE}')) then
        Result := MsgBox('The folder ' + DataDir() + ' already exists outside your user folder, so other ' +
        'accounts on this PC may be able to read what''s in it, including the API keys Baihe Studio ' +
        'saves there.' + #13#10#13#10 + 'Use it anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
      // A folder that already holds data this install didn't put there
      // (an earlier install's own folder is not asked about). Installing
      // and updating never delete it; only uninstalling can, and only the
      // ticked items by name. The folder itself is never removed, because
      // Setup didn't create it.
      if Result then
      begin
        Existing := ExistingDataItems(DataDir());
        if (Existing <> '') and (NormDir(OldDir) <> NormDir(DataDir())) then
          Result := MsgBox('The folder ' + DataDir() + ' already has:' + Existing + #13#10#13#10 +
            'Baihe Studio will use these as they are, so an existing library and API keys will show ' +
            'up in the app. Installing and updating never delete them.' + #13#10#13#10 +
            'Uninstalling keeps them unless you tick "delete" for them in the uninstaller. If you ' +
            'do, the ticked items above are permanently deleted, including anything you put in them ' +
            'yourself. Other files in the folder are never deleted.' + #13#10#13#10 +
            'Use this folder?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
      end;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine +
    'Your data (library, settings and API keys, downloaded models):' + NewLine + Space + DataDir();
  if MemoGroupInfo <> '' then
    Result := Result + NewLine + NewLine + MemoGroupInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
end;

// Stops a server this install's launcher started (and everything it
// started), so its files can be replaced or removed. Harmless when
// nothing is running or installed.
procedure StopRunningServer(const AppDir: String);
var
  ResultCode: Integer;
begin
  if FileExists(AppDir + '\python\python.exe') and FileExists(AppDir + '\app\installer\launcher.py') then
    Exec(AppDir + '\python\python.exe', '-s "' + AppDir + '\app\installer\launcher.py" --stop',
      AppDir + '\app', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Where service.py keeps everything it runs with administrator rights.
function AdminDir(): String;
begin
  Result := ExpandConstant('{commonpf64}') + '\Baihe Studio Services';
end;

// True if the BaiheStudio Windows service exists. Reading that needs no
// administrator rights; sc.exe exits 1060 when there's no such service.
function ServiceInstalled(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\sc.exe'), 'query ' + AppServiceName, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// True if the BaiheStudio service or its admin folder is still there: a
// half-finished uninstall leaves one without the other, and uninstall can be
// run again over either.
function ServiceOrAdminDirPresent(): Boolean;
begin
  Result := ServiceInstalled() or FileExists(AdminDir() + '\helper\python\python.exe');
end;

// Runs service.py with administrator rights: directly when Setup already
// has them, otherwise through the Windows permission prompt (only this step
// is elevated; the install itself stays per-user). Base is a folder with
// helper\python and helper\lib\installer\service.py: Setup's own {app}\service
// for `install`, the admin folder for everything else. False if it couldn't
// run or didn't succeed.
function RunServiceHelper(const Base, Args: String): Boolean;
var
  Exe, Params: String;
  ResultCode: Integer;
begin
  Exe := Base + '\helper\python\python.exe';
  Params := '-I -S "' + Base + '\helper\lib\installer\service.py" ' + Args;
  Log('Running the service step: python.exe ' + Params);
  if not FileExists(Exe) then
  begin
    Log('Service step: ' + Exe + ' is missing.');
    Result := False;
    Exit;
  end;
  if IsAdmin() then
    Result := Exec(Exe, Params, ExpandConstant('{sys}'), SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    Result := ShellExec('runas', Exe, Params, ExpandConstant('{sys}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('Service step result: ' + IntToStr(ResultCode));
  Result := Result and (ResultCode = 0);
end;

// An update replaces files the service holds open, so it stops first,
// through the admin copy of the install being replaced. '' when done or
// when there's nothing to stop.
function StopBackgroundService(): String;
begin
  Result := '';
  if not ServiceInstalled() then
    Exit;
  // Without its helper no prompt is ever shown, and the service stays
  // installed whatever the owner stops, so every rerun would fail the same way.
  if not FileExists(AdminDir() + '\helper\python\python.exe') then
    Result := 'Setup couldn''t stop Baihe Studio''s background service, so its files can''t be ' +
      'replaced: ' + AdminDir() + ' was damaged. An administrator can remove the service ' +
      'with: sc delete ' + AppServiceName + ', then run Setup again.'
  else if not RunServiceHelper(AdminDir(), 'stop') then
    Result := 'Setup couldn''t stop Baihe Studio''s background service, so its files can''t be ' +
      'replaced. Allow the administrator prompt, or stop the "Baihe Studio" service in ' +
      'Services, then run Setup again.';
end;

// '' if Setup can create the data folder and write to it (an unplugged
// drive or a folder this user can't write is caught before any file is
// replaced, not after).
function DataDirWriteProblem(const Dir: String): String;
var
  Probe: String;
begin
  Result := '';
  Probe := AddBackslash(Dir) + 'baihe-setup-write-check.tmp';
  if not ForceDirectories(Dir) then
    Result := 'Setup couldn''t create your data folder ' + Dir + '. Choose a folder you can write to.'
  else if not SaveStringToFile(Probe, 'ok', False) then
    Result := 'Setup can''t write to your data folder ' + Dir + '. Choose a folder you can write to.'
  else
    DeleteFile(Probe);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  OldDir: String;
  OldCreated: Boolean;
begin
  // Checked here too: NextButtonClick doesn't run for a silent install.
  Result := InstallDirProblem(ExpandConstant('{app}'));
  if Result = '' then
    Result := DataDirProblem(DataDir(), ExpandConstant('{app}'));
  if Result <> '' then
    Exit;
  // Whether Baihe Studio owns the data folder (Setup is about to create
  // it, or an earlier install did): only then may a clean uninstall
  // remove the folder itself. Read before the old marker is replaced.
  OldDir := ReadMarker(ExpandConstant('{app}\app\INSTALLED'), ExpandConstant('{app}'), OldCreated);
  DataDirIsNew := not DirExists(DataDir());
  DataDirCreatedBySetup := DataDirIsNew or
    (OldCreated and (NormDir(OldDir) = NormDir(DataDir())));
  Result := DataDirWriteProblem(DataDir());
  if Result = '' then
    Result := StopBackgroundService();
  if Result = '' then
    StopRunningServer(ExpandConstant('{app}'));
end;

function PackagesInstalled(): Boolean;
begin
  Result := not PostInstallFailed;
end;

procedure RunPostInstall();
var
  ResultCode: Integer;
  Params: String;
begin
  WizardForm.StatusLabel.Caption := 'Installing Baihe Studio''s Python packages (this can take a few minutes)...';
  WizardForm.FilenameLabel.Caption := '';
  Params := '-s "' + ExpandConstant('{app}\app\installer\postinstall.py') + '"' +
    ' --wheels "' + ExpandConstant('{app}\wheels') + '"' +
    ' --data-dir "' + DataDir() + '"';
  if DataDirCreatedBySetup then
    Params := Params + ' --data-dir-created';
  // Only a folder made just now is limited to this account; an update
  // leaves the permissions the user may have adjusted since.
  if DataDirIsNew then
    Params := Params + ' --data-dir-new';
  Log('Running the install step: python.exe ' + Params);
  if not Exec(ExpandConstant('{app}\python\python.exe'), Params, ExpandConstant('{app}\app'),
      SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;
  Log('Install step exit code: ' + IntToStr(ResultCode));
  if ResultCode <> 0 then
  begin
    PostInstallFailed := True;
    SuppressibleMsgBox('Setup copied Baihe Studio''s files, but installing its Python packages failed (code ' +
      IntToStr(ResultCode) + ').' + #13#10#13#10 + 'Details are in ' + DataDir() + '\launcher\install.log.' + #13#10#13#10 +
      'Run Setup again to retry. Your data was not touched.', mbError, MB_OK, IDOK);
  end;
end;

// The port the owner chose for Baihe Studio: the user's BAIHE_API_PORT. '' when
// it isn't set (the default port). service.py uses it only on a fresh install:
// an update keeps the service's stored port and ignores it. Once the service
// exists the launcher follows that stored port, which the "Baihe Studio
// service" menu (service.py set-port) changes. Only digits are passed on the
// elevated command line; anything else is passed as a word service.py
// refuses on a fresh install (and ignores on an update).
function ApiPortArg(): String;
var
  Port: String;
  I: Integer;
  Digits: Boolean;
begin
  Result := '';
  Port := Trim(GetEnv('BAIHE_API_PORT'));
  if Port = '' then
    Exit;
  Digits := Length(Port) <= 5;
  for I := 1 to Length(Port) do
    if (Port[I] < '0') or (Port[I] > '9') then
      Digits := False;
  if not Digits then
    Port := 'not-a-number';
  Result := ' --port ' + Port;
end;

// With the "service" task: create or refresh the service and start it.
// Without it: remove a service an earlier install made. The app still
// works either way, from the Start menu.
procedure ConfigureService();
begin
  if WizardIsTaskSelected('service') then
  begin
    WizardForm.StatusLabel.Caption := 'Setting up Baihe Studio''s background service...';
    ServiceFailed := not RunServiceHelper(ExpandConstant('{app}\service'),
      '--install-root "' + ExpandConstant('{app}') + '" --data-dir "' + DataDir() + '" install' +
      ApiPortArg());
  end
  else if ServiceOrAdminDirPresent() then
    ServiceFailed := not RunServiceHelper(AdminDir(), 'uninstall');
  if ServiceFailed then
    SuppressibleMsgBox('Baihe Studio is installed, but its background service couldn''t be set up ' +
      'or removed (the administrator prompt may have been declined).' + #13#10#13#10 +
      'You can still start Baihe Studio from the Start menu. Run Setup again to retry; details ' +
      'are in ' + AdminDir() + '\service.log.', mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // A full install replaces the versions an app-only update kept; checked
  // here, before the new manifest.json is copied in.
  if (CurStep = ssInstall) and IsBaiheInstallDir(ExpandConstant('{app}')) then
    DeleteAppVersionFolders(ExpandConstant('{app}'));
  if CurStep = ssPostInstall then
  begin
    RunPostInstall();
    if not PostInstallFailed then
      ConfigureService();
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  // 100 and 101: outside the codes Inno Setup itself uses (1-8).
  if PostInstallFailed then
    Result := 100
  else if ServiceFailed then
    Result := 101
  else
    Result := 0;
end;

// ---------------- Uninstall ----------------

function AddCheckBox(Form: TSetupForm; const Caption: String; Top: Integer): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(Form);
  Result.Parent := Form;
  Result.Left := ScaleX(16);
  Result.Top := Top;
  Result.Width := Form.ClientWidth - ScaleX(32);
  Result.Height := ScaleY(20);
  Result.Caption := Caption;
  Result.Checked := False;
end;

// "Remove everything" ticks the three boxes above it; unticking it puts
// them back the way they were.
procedure CleanBoxClick(Sender: TObject);
begin
  if CleanBox.Checked then
  begin
    PrevLibrary := LibraryBox.Checked;
    PrevSettings := SettingsBox.Checked;
    PrevModels := ModelsBox.Checked;
    LibraryBox.Checked := True;
    SettingsBox.Checked := True;
    ModelsBox.Checked := True;
  end
  else
  begin
    LibraryBox.Checked := PrevLibrary;
    SettingsBox.Checked := PrevSettings;
    ModelsBox.Checked := PrevModels;
  end;
end;

// Asks what, beyond the program, to delete. Every box starts unticked;
// "Remove everything" needs a second confirmation naming the data folder.
// Returns False if the user cancels the uninstall.
function AskWhatToDelete(): Boolean;
var
  Form: TSetupForm;
  Intro: TNewStaticText;
  OkButton, CancelButton: TNewButton;
  Confirmed: Boolean;
  CleanCaption, CleanWarning: String;
begin
  PrevLibrary := False;
  PrevSettings := False;
  PrevModels := False;
  if UninstDataDirCreated then
  begin
    // Setup made this folder, so a clean uninstall removes all of it --
    // including anything the user has since put there themselves.
    CleanCaption := 'Remove everything (clean uninstall): all of the above and the whole data folder';
    CleanWarning := 'This permanently deletes the folder' + #13#10 + UninstDataDir + #13#10 +
      'and EVERYTHING in it, including any files you put there yourself, as well as your ' +
      'library, settings and API keys, and downloaded models.';
  end
  else
  begin
    CleanCaption := 'Remove everything (clean uninstall): all of the above and the rest of Baihe Studio''s files';
    CleanWarning := 'This permanently deletes your library, settings and API keys, downloaded models ' +
      'and Baihe Studio''s other files in' + #13#10 + UninstDataDir + #13#10 +
      '(anything else in that folder is left alone).';
  end;
  Form := CreateCustomForm(ScaleX(500), ScaleY(290), False, False);
  try
    Form.Caption := 'Uninstall {#AppName}';

    Intro := TNewStaticText.Create(Form);
    Intro.Parent := Form;
    Intro.Left := ScaleX(16);
    Intro.Top := ScaleY(14);
    Intro.Width := Form.ClientWidth - ScaleX(32);
    Intro.AutoSize := False;
    Intro.Height := ScaleY(64);
    Intro.WordWrap := True;
    Intro.Caption := 'The program will be removed. Your data in ' + UninstDataDir +
      ' is kept unless you tick a box below. Ticked items are deleted for good.';

    LibraryBox := AddCheckBox(Form, 'Also delete my library (all projects, translations, audio/video, backups)', ScaleY(84));
    SettingsBox := AddCheckBox(Form, 'Also delete my saved settings and API keys (.env)', ScaleY(112));
    ModelsBox := AddCheckBox(Form, 'Also delete downloaded AI models (model_cache)', ScaleY(140));
    CleanBox := AddCheckBox(Form, CleanCaption, ScaleY(178));
    CleanBox.OnClick := @CleanBoxClick;

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := 'Uninstall';
    OkButton.Width := ScaleX(96);
    OkButton.Height := ScaleY(28);
    OkButton.Left := Form.ClientWidth - ScaleX(16 + 96 + 8 + 96);
    OkButton.Top := Form.ClientHeight - ScaleY(16 + 28);
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := 'Cancel';
    CancelButton.Width := ScaleX(96);
    CancelButton.Height := ScaleY(28);
    CancelButton.Left := Form.ClientWidth - ScaleX(16 + 96);
    CancelButton.Top := Form.ClientHeight - ScaleY(16 + 28);
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;

    repeat
      Result := Form.ShowModal() = mrOk;
      Confirmed := True;
      if Result and CleanBox.Checked then
      begin
        Confirmed := MsgBox('Remove everything?' + #13#10#13#10 + CleanWarning + #13#10#13#10 +
          'It can''t be undone.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
        if not Confirmed then
        begin
          // Back to what the user had ticked before "Remove everything"
          // (explicitly: whether or not unticking fires OnClick).
          CleanBox.Checked := False;
          LibraryBox.Checked := PrevLibrary;
          SettingsBox.Checked := PrevSettings;
          ModelsBox.Checked := PrevModels;
        end;
      end;
    until Confirmed;
    if Result then
    begin
      CleanAll := CleanBox.Checked;
      DeleteLibrary := LibraryBox.Checked or CleanAll;
      DeleteSettings := SettingsBox.Checked or CleanAll;
      DeleteModels := ModelsBox.Checked or CleanAll;
    end;
  finally
    Form.Free();
  end;
end;

function HasCleanSwitch(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/CLEAN') = 0 then
      Result := True;
end;

// The service runs from the admin folder and is pointed at the program
// folder, so it goes before the program does (administrator rights, through
// service.py, which also takes the service's permissions off the program and
// data folders). If that can't be done the uninstall stops, rather than
// leave a service pointing at deleted files.
function RemoveService(): Boolean;
begin
  Result := True;
  if not ServiceOrAdminDirPresent() then
    Exit;
  Result := RunServiceHelper(AdminDir(), 'uninstall');
  if not Result then
  begin
    Log('Couldn''t remove the Baihe Studio service; uninstall stopped.');
    if not UninstallSilent() then
      MsgBox('Baihe Studio''s background service couldn''t be removed (the administrator ' +
        'prompt may have been declined), so nothing was uninstalled.' + #13#10#13#10 +
        'Run the uninstaller again and allow the prompt. If ' + AdminDir() + ' was damaged, ' +
        'an administrator can remove the service with: sc delete ' + AppServiceName, mbError, MB_OK);
  end;
end;

// Total size of the files under Dir, for the "how much am I about to lose"
// text; unreadable entries count as zero.
function FolderBytes(const Dir: String): Int64;
var
  Rec: TFindRec;
  Path: String;
  Size: Int64;
begin
  Result := 0;
  if FindFirst(AddBackslash(Dir) + '*', Rec) then
  try
    repeat
      if (Rec.Name = '.') or (Rec.Name = '..') then
        Continue;
      Path := AddBackslash(Dir) + Rec.Name;
      if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        // A junction or symlink could lead outside the folder or loop.
        if (Rec.Attributes and $400) = 0 then // FILE_ATTRIBUTE_REPARSE_POINT
          Result := Result + FolderBytes(Path);
      end
      else if FileSize64(Path, Size) then
        Result := Result + Size;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

function SizeText(Bytes: Int64): String;
begin
  if Bytes >= 1073741824 then
    Result := Format('%d.%d GB', [Bytes div 1073741824, (Bytes mod 1073741824) * 10 div 1073741824])
  else
    Result := Format('%d MB', [Bytes div 1048576]);
end;

function AnyFolderMatches(const Dir, Pattern: String): Boolean;
var
  Rec: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(Dir) + Pattern, Rec) then
  try
    repeat
      if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        Result := True;
    until Result or not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

// Size of the folders directly in Dir whose names match Pattern; a junction
// or symlink counts as zero because it is never followed.
function MatchingFoldersBytes(const Dir, Pattern: String): Int64;
var
  Rec: TFindRec;
begin
  Result := 0;
  if FindFirst(AddBackslash(Dir) + Pattern, Rec) then
  try
    repeat
      if ((Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and ((Rec.Attributes and $400) = 0) then
        Result := Result + FolderBytes(AddBackslash(Dir) + Rec.Name);
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

function HasRestoreCopies(): Boolean;
begin
  Result := AnyFolderMatches(UninstDataDir, 'library.pre_restore_*') or
    AnyFolderMatches(UninstDataDir, '.restore_staging_*') or
    FileExists(UninstDataDir + '\library.restore-lock');
end;

// Inno Setup's Pascal Script has no InputQuery (ISCC rejects it as an unknown
// identifier), so the typed confirmation is its own form. Returns True only
// for the button that confirms, with the text as typed; Cancel, Esc or
// closing the window give False and an empty Typed. Instruction gets its own
// line right above the box: in the wrapped Prompt it could be clipped by a
// long path and leave the user not knowing what to type.
function AskTypedWord(const Title, Prompt, Instruction: String; var Typed: String): Boolean;
var
  Form: TSetupForm;
  PromptText, InstructionText: TNewStaticText;
  Edit: TNewEdit;
  OkButton, CancelButton: TNewButton;
begin
  Typed := '';
  Form := CreateCustomForm(ScaleX(460), ScaleY(300), False, False);
  try
    Form.Caption := Title;

    PromptText := TNewStaticText.Create(Form);
    PromptText.Parent := Form;
    PromptText.Left := ScaleX(16);
    PromptText.Top := ScaleY(14);
    PromptText.Width := Form.ClientWidth - ScaleX(32);
    PromptText.AutoSize := False;
    PromptText.Height := ScaleY(144);
    PromptText.WordWrap := True;
    PromptText.Caption := Prompt;

    InstructionText := TNewStaticText.Create(Form);
    InstructionText.Parent := Form;
    InstructionText.Left := ScaleX(16);
    InstructionText.Top := ScaleY(164);
    InstructionText.Width := Form.ClientWidth - ScaleX(32);
    InstructionText.AutoSize := False;
    InstructionText.Height := ScaleY(32);
    InstructionText.WordWrap := True;
    InstructionText.Caption := Instruction;

    Edit := TNewEdit.Create(Form);
    Edit.Parent := Form;
    Edit.Left := ScaleX(16);
    Edit.Top := ScaleY(200);
    Edit.Width := Form.ClientWidth - ScaleX(32);
    Edit.Text := '';

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := 'OK';
    OkButton.Width := ScaleX(96);
    OkButton.Height := ScaleY(28);
    OkButton.Left := Form.ClientWidth - ScaleX(16 + 96 + 8 + 96);
    OkButton.Top := Form.ClientHeight - ScaleY(16 + 28);
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := 'Cancel';
    CancelButton.Width := ScaleX(96);
    CancelButton.Height := ScaleY(28);
    CancelButton.Left := Form.ClientWidth - ScaleX(16 + 96);
    CancelButton.Top := Form.ClientHeight - ScaleY(16 + 28);
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;

    Form.ActiveControl := Edit;
    Result := Form.ShowModal() = mrOk;
    if Result then
      Typed := Edit.Text;
  finally
    Form.Free();
  end;
end;

// Deleting the library takes its backups with it (they are inside it by
// default), so a ticked box alone is not enough: the exact word, in capitals,
// must be typed. Anything else, including Cancel, keeps it.
function ConfirmLibraryDelete(): Boolean;
var
  Typed, Copies: String;
  Total: Int64;
begin
  Typed := '';
  Copies := '';
  Total := FolderBytes(UninstDataDir + '\library');
  // Only named when the library exists: the copies are deleted with it, and
  // without it they may be the only good copy (see DeleteLibraryNeighbours).
  if DirExists(UninstDataDir + '\library') and HasRestoreCopies() then
  begin
    Total := Total + MatchingFoldersBytes(UninstDataDir, 'library.pre_restore_*') +
      MatchingFoldersBytes(UninstDataDir, '.restore_staging_*');
    Copies := #13#10#13#10 + 'It also deletes the copies a library restore left beside it ' +
      '(library.pre_restore_*, .restore_staging_*, library.restore-lock). Together: ' + SizeText(Total) + '.';
  end;
  Result := AskTypedWord('Delete your library?',
    'Your library (' + UninstDataDir + '\library) is ' + SizeText(FolderBytes(UninstDataDir + '\library')) +
    '. It holds your projects, translations, audio and video, and your backups too - ' +
    'backups are inside it by default. This can''t be undone.' + Copies,
    'Type DELETE in capital letters to delete it. Anything else keeps it.', Typed) and
    (CompareStr(Typed, 'DELETE') = 0);
end;

function InitializeUninstall(): Boolean;
var
  Extra: String;
begin
  Result := True;
  DeleteLibrary := False;
  DeleteSettings := False;
  DeleteModels := False;
  CleanAll := False;
  // Only folders Baihe Studio put there are ever removed (see InstallDirProblem).
  ProgramOwned := IsBaiheInstallDir(ExpandConstant('{app}')) and
    FileExists(ExpandConstant('{app}\app\installer\launcher.py'));
  UninstDataDir := ReadMarker(ExpandConstant('{app}\app\INSTALLED'), ExpandConstant('{app}'),
    UninstDataDirCreated);
  if UninstallSilent() then
  begin
    // A silent uninstall keeps all user data unless /CLEAN is passed.
    if HasCleanSwitch() then
    begin
      CleanAll := True;
      DeleteLibrary := True;
      DeleteSettings := True;
      DeleteModels := True;
    end;
  end
  else if (UninstDataDir <> '') and DirExists(UninstDataDir) then
  begin
    Result := AskWhatToDelete();
    if Result and DeleteLibrary and not ConfirmLibraryDelete() then
    begin
      // "Remove everything" would delete the whole data folder, library
      // included, so it can't stand once the library is kept. The settings
      // and models it ticked go back to what the user had chosen themselves,
      // as when they answer No to its own confirmation.
      if CleanAll then
      begin
        DeleteSettings := PrevSettings;
        DeleteModels := PrevModels;
      end;
      CleanAll := False;
      DeleteLibrary := False;
      Extra := '';
      if DeleteSettings then
        Extra := Extra + #13#10 + '  - your settings and API keys (.env)';
      if DeleteModels then
        Extra := Extra + #13#10 + '  - downloaded AI models (model_cache)';
      if Extra = '' then
        MsgBox('Your library was kept: you did not type DELETE. Nothing else of yours will be deleted.',
          mbInformation, MB_OK)
      else
        MsgBox('Your library was kept: you did not type DELETE. These will still be deleted, ' +
          'because you ticked them:' + Extra, mbInformation, MB_OK);
    end;
  end;
  if Result then
    Result := RemoveService();
end;

procedure AddLine(var List: String; const Line: String);
begin
  List := List + #13#10 + '  - ' + Line;
end;

procedure DeleteTree(const Path, Name: String; var Removed, Left: String);
begin
  if DirExists(Path) then
  begin
    if IsLink(Path) then
    begin
      if RemoveDir(Path) then
        AddLine(Removed, Name + ' - only the link at ' + Path + '; the folder it points to was kept')
      else
        AddLine(Left, Name + ' - a link at ' + Path + ' that couldn''t be removed; the folder it points to was kept');
    end
    else if DelTreeNoLinks(Path) and not DirExists(Path) then
      AddLine(Removed, Name)
    else
      AddLine(Left, Name + ' - couldn''t be removed completely (a file may be in use)');
  end;
end;

// Deletes the %TEMP% items matching Pattern (folders, or files when
// Files is set). Returns how many.
function DeleteTempMatches(const Pattern: String; Files: Boolean): Integer;
var
  Temp: String;
  Rec: TFindRec;
  IsDir: Boolean;
begin
  Result := 0;
  Temp := GetEnv('TEMP');
  if Temp = '' then
    Exit;
  if FindFirst(AddBackslash(Temp) + Pattern, Rec) then
  try
    repeat
      IsDir := (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0;
      if IsDir and not Files then
      begin
        if DelTreeNoLinks(AddBackslash(Temp) + Rec.Name) then
          Result := Result + 1;
      end
      else if Files and not IsDir then
      begin
        if DeleteFile(AddBackslash(Temp) + Rec.Name) then
          Result := Result + 1;
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

// Baihe Studio's own named leftovers in %TEMP%: work folders named
// baihe_*, extension page files baihe_page_*, and torch pin lists
// baihe-torch-pins-*. (Temporary folders Python names itself, tmp*, can't
// be told apart from other programs' and are left alone.) Returns how many.
function DeleteOwnTempItems(): Integer;
begin
  Result := DeleteTempMatches('baihe_*', False) + DeleteTempMatches('baihe_page_*', True) +
    DeleteTempMatches('baihe-torch-pins-*', True);
end;

function EnvOrDefault(const Name, Fallback: String): String;
begin
  Result := GetEnv(Name);
  if Result = '' then
    Result := Fallback;
end;

procedure NoteShared(const Path, Name: String; var Left: String);
begin
  if DirExists(Path) then
    AddLine(Left, Name + ' (' + Path + ', ' + SizeText(FolderBytes(Path)) + ') - shared with other programs, never deleted here');
end;

// Deletes the folders directly in Dir whose names match Pattern. A junction
// or symlink is removed as a link only: recursing into it could leave Dir and
// delete what it points to. Returns how many.
function DeleteMatchingFolders(const Dir, Pattern: String): Integer;
var
  Rec: TFindRec;
  Path: String;
begin
  Result := 0;
  if FindFirst(AddBackslash(Dir) + Pattern, Rec) then
  try
    repeat
      Path := AddBackslash(Dir) + Rec.Name;
      if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        if (Rec.Attributes and $400) <> 0 then // FILE_ATTRIBUTE_REPARSE_POINT
        begin
          if RemoveDir(Path) then
            Result := Result + 1;
        end
        else if DelTreeNoLinks(Path) then
          Result := Result + 1;
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

// What a library restore leaves beside the library: its lock file, the
// previous library and an unfinished staging folder (db/library_folder.py,
// services/library_restore_service.py). Called only when the library is being
// deleted, and does nothing unless it is gone: until then a pre_restore
// folder may be the only good copy of it.
procedure DeleteLibraryNeighbours(var Removed, Left: String);
var
  Count: Integer;
begin
  if DirExists(UninstDataDir + '\library') then
    Exit;
  if FileExists(UninstDataDir + '\library.restore-lock') then
  begin
    if DeleteFile(UninstDataDir + '\library.restore-lock') then
      AddLine(Removed, 'the library restore lock (library.restore-lock)')
    else
      AddLine(Left, 'library.restore-lock - couldn''t be deleted; remove it by hand');
  end;
  Count := DeleteMatchingFolders(UninstDataDir, 'library.pre_restore_*') +
    DeleteMatchingFolders(UninstDataDir, '.restore_staging_*');
  if Count > 0 then
    AddLine(Removed, IntToStr(Count) + ' folder(s) a library restore left beside it (library.pre_restore_*, .restore_staging_*)');
  // Not deleted: a copy kept before a Reset library (library.pre_reset_*) is
  // outside what the owner asked this to remove; NoteKeptData reports it.
end;

// What is still in the data folder once the steps above have run, by name
// and size only. No file is opened, so the settings file's keys are never
// shown.
procedure NoteKeptData(var Left: String);
begin
  if DirExists(UninstDataDir + '\library') then
    AddLine(Left, 'your library (' + UninstDataDir + '\library, ' +
      SizeText(FolderBytes(UninstDataDir + '\library')) + ')');
  if FileExists(UninstDataDir + '\.env') then
    AddLine(Left, 'your settings and API keys (.env) in ' + UninstDataDir + ' (contents are never shown)');
  if DirExists(UninstDataDir + '\model_cache') then
    AddLine(Left, 'downloaded AI models (' + UninstDataDir + '\model_cache, ' +
      SizeText(FolderBytes(UninstDataDir + '\model_cache')) + ')');
  if FileExists(UninstDataDir + '\vtuber_names.json') then
    AddLine(Left, 'vtuber_names.json - couldn''t be deleted; remove it by hand');
  if HasRestoreCopies() then
  begin
    if DirExists(UninstDataDir + '\library') then
      AddLine(Left, 'library copies a restore left beside it (library.pre_restore_*, .restore_staging_*, library.restore-lock); they go when you delete the library')
    else
      AddLine(Left, 'library copies a restore left beside it (library.pre_restore_*, .restore_staging_*, library.restore-lock) and no library folder: one of them may hold your real library, so none was deleted');
  end;
  if AnyFolderMatches(UninstDataDir, 'library.pre_reset_*') then
    AddLine(Left, 'a copy of the library kept before a reset (library.pre_reset_*); delete it by hand if you no longer need it');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Removed, Left, Summary: String;
  Temps: Integer;
  LibraryExisted: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  Removed := '';
  Left := '';

  // The program: pip-installed packages and .pyc files aren't in Inno's own
  // file list, so the two program folders go explicitly -- but only when
  // Baihe Studio put them there.
  if ProgramOwned then
  begin
    DeleteTree(ExpandConstant('{app}\python'), 'the bundled Python and its packages', Removed, Left);
    DeleteTree(ExpandConstant('{app}\app'), 'the program files', Removed, Left);
    DeleteTree(ExpandConstant('{app}\service'), 'the background service''s files', Removed, Left);
    DeleteTree(ExpandConstant('{app}\app-update'), 'the app update''s work folder', Removed, Left);
    if DeleteAppVersionFolders(ExpandConstant('{app}')) > 0 then
      AddLine(Removed, 'earlier app versions kept by app updates');
  end;
  RemoveDir(ExpandConstant('{app}'));

  if UninstDataDir <> '' then
  begin
    if CleanAll and UninstDataDirCreated then
    begin
      // Setup created this folder, so everything in it is Baihe Studio's.
      DeleteTree(UninstDataDir, 'the data folder ' + UninstDataDir + ' and everything in it', Removed, Left);
    end
    else
    begin
      // Only these named items, never the folder's other contents: the
      // folder may be one the user picked and shares with other files.
      LibraryExisted := DirExists(UninstDataDir + '\library');
      if DeleteLibrary then
      begin
        DeleteTree(UninstDataDir + '\library', 'your library (projects, backups, logs, caches, browser profiles)', Removed, Left);
        // Items cleared from the library wait here until deleted from Trash.
        DeleteTree(UninstDataDir + '\baihe_trash', 'items you moved to Trash', Removed, Left);
      end;
      // Only when a library existed and is now gone: with none, a
      // library.pre_restore_* folder may hold the real library.
      if DeleteLibrary and LibraryExisted then
      begin
        DeleteLibraryNeighbours(Removed, Left);
      end;
      if DeleteSettings and FileExists(UninstDataDir + '\.env') then
        if DeleteFile(UninstDataDir + '\.env') then
          AddLine(Removed, 'your settings and API keys (.env)')
        else
          AddLine(Left, 'your settings and API keys (.env) - couldn''t be deleted; remove it by hand');
      if DeleteModels then
        DeleteTree(UninstDataDir + '\model_cache', 'downloaded AI models', Removed, Left);
      // Baihe's downloaded VTuber name list and its half-written download:
      // these two names only, never a *.part pattern, on every uninstall. The
      // folder may be one the user picked and shares with other files.
      if FileExists(UninstDataDir + '\vtuber_names.json') then
        if not DeleteFile(UninstDataDir + '\vtuber_names.json') then
          AddLine(Left, 'vtuber_names.json - couldn''t be deleted; remove it by hand');
      DeleteFile(UninstDataDir + '\vtuber_names.json.part');
      // The launcher's own files (not user data), by name, then its folder
      // only if that leaves it empty.
      DeleteFile(UninstDataDir + '\launcher\server.pid');
      DeleteFile(UninstDataDir + '\launcher\shutdown.token');
      DeleteFile(UninstDataDir + '\launcher\starting.lock');
      DeleteFile(UninstDataDir + '\launcher\server.log');
      DeleteFile(UninstDataDir + '\launcher\install.log');
      RemoveDir(UninstDataDir + '\launcher');
      // Removed only if nothing else is left in it.
      RemoveDir(UninstDataDir);
      if CleanAll and DirExists(UninstDataDir) then
        AddLine(Left, 'other files in ' + UninstDataDir + ' that Baihe Studio didn''t make');
    end;
  end;

  if (UninstDataDir <> '') and DirExists(UninstDataDir) then
    NoteKeptData(Left);

  if CleanAll then
  begin
    Temps := DeleteOwnTempItems();
    if Temps > 0 then
      AddLine(Removed, IntToStr(Temps) + ' of Baihe Studio''s temporary items in %TEMP% (baihe_*)');
  end
  else
    AddLine(Left, 'Baihe Studio''s temporary items in %TEMP% (baihe_*), deleted only by a clean uninstall');
  AddLine(Removed, 'the Start menu and desktop shortcuts, and the Apps entry');
  NoteShared(ExpandConstant('{localappdata}\ms-playwright'), 'Playwright browsers', Left);
  NoteShared(ExpandConstant('{localappdata}\pip\cache'), 'pip''s download cache', Left);
  NoteShared(EnvOrDefault('HF_HOME', ExpandConstant('{%USERPROFILE}\.cache\huggingface')), 'Hugging Face''s model cache', Left);
  NoteShared(EnvOrDefault('TORCH_HOME', ExpandConstant('{%USERPROFILE}\.cache\torch')), 'PyTorch''s cache', Left);
  NoteShared(EnvOrDefault('OLLAMA_MODELS', ExpandConstant('{%USERPROFILE}\.ollama\models')), 'Ollama models', Left);
  NoteShared(ExpandConstant('{%USERPROFILE}\.deno'), 'Deno (the JavaScript runtime Diagnostics can install)', Left);
  AddLine(Left, 'Ollama itself, if you use it (Baihe Studio never installed it)');
  AddLine(Left, 'Python, Git, ffmpeg and Node.js, if you installed them with winget (remove them in Settings > Apps)');
  AddLine(Left, 'temporary files other programs could also have made (%TEMP%\tmp*)');
  AddLine(Left, 'automatic backups you pointed at a folder outside the data folder, if any');

  if CleanAll then
    Summary := 'Clean uninstall finished.'
  else
    Summary := 'Uninstall finished.';
  Summary := Summary + #13#10#13#10 + 'Removed:' + Removed;
  if Left <> '' then
    Summary := Summary + #13#10#13#10 + 'Left on this PC:' + Left;
  Log(Summary);
  if not UninstallSilent() then
    MsgBox(Summary, mbInformation, MB_OK);
end;
