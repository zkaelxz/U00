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
; Silent install (CI, power users):
;   BaiheStudio-Setup-<v>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="..." /DATADIR="..."
; Setup exits with code 100 if the files were copied but the Python packages
; didn't install (details in <data>\launcher\install.log).
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the code wholesale (so a deleted module can't linger)
; but keeps {app}\python, where optional packages added through Diagnostics live.
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#PayloadDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\wheels\*"; DestDir: "{tmp}\wheels"; Flags: ignoreversion deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\app_icon.ico"; Comment: "Start Baihe Studio and open it in its own window"
Name: "{group}\Stop {#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"" --stop"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\app_icon.ico"; Comment: "Stop Baihe Studio's server"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\assets\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"""; WorkingDir: "{app}\app"; Description: "Start Baihe Studio now"; Flags: postinstall nowait skipifsilent; Check: PackagesInstalled

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "-s ""{app}\app\installer\launcher.py"" --stop"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "StopServer"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  PostInstallFailed: Boolean;
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
  Problem, OldDir: String;
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
    ' --wheels "' + ExpandConstant('{tmp}\wheels') + '"' +
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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunPostInstall();
end;

function GetCustomSetupExitCode(): Integer;
begin
  // 100: outside the codes Inno Setup itself uses (1-8).
  if PostInstallFailed then
    Result := 100
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

function InitializeUninstall(): Boolean;
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
    Result := AskWhatToDelete();
end;

procedure AddLine(var List: String; const Line: String);
begin
  List := List + #13#10 + '  - ' + Line;
end;

procedure DeleteTree(const Path, Name: String; var Removed, Left: String);
begin
  if DirExists(Path) then
  begin
    if DelTree(Path, True, True, True) and not DirExists(Path) then
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
        if DelTree(AddBackslash(Temp) + Rec.Name, True, True, True) then
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

procedure NoteShared(const Path, Name: String; var Left: String);
begin
  if DirExists(Path) then
    AddLine(Left, Name + ' (' + Path + ') - shared with other programs');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Removed, Left, Summary: String;
  Temps: Integer;
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
      if DeleteLibrary then
        DeleteTree(UninstDataDir + '\library', 'your library (projects, backups, logs, caches, browser profiles)', Removed, Left);
      if DeleteSettings and FileExists(UninstDataDir + '\.env') then
        if DeleteFile(UninstDataDir + '\.env') then
          AddLine(Removed, 'your settings and API keys (.env)');
      if DeleteModels then
        DeleteTree(UninstDataDir + '\model_cache', 'downloaded AI models', Removed, Left);
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

  if not CleanAll then
    Exit;

  Temps := DeleteOwnTempItems();
  if Temps > 0 then
    AddLine(Removed, IntToStr(Temps) + ' of Baihe Studio''s temporary items in %TEMP% (baihe_*)');
  AddLine(Removed, 'the Start menu and desktop shortcuts, and the Apps entry');
  NoteShared(ExpandConstant('{localappdata}\ms-playwright'), 'Playwright browsers', Left);
  NoteShared(ExpandConstant('{localappdata}\pip\cache'), 'pip''s download cache', Left);
  NoteShared(ExpandConstant('{%USERPROFILE}\.cache\huggingface'), 'Hugging Face''s default model cache', Left);
  NoteShared(ExpandConstant('{%USERPROFILE}\.deno'), 'Deno (the JavaScript runtime Diagnostics can install)', Left);
  AddLine(Left, 'temporary files other programs could also have made (%TEMP%\tmp*)');
  AddLine(Left, 'automatic backups you pointed at a folder outside the data folder, if any');

  Summary := 'Clean uninstall finished.' + #13#10#13#10 + 'Removed:' + Removed;
  if Left <> '' then
    Summary := Summary + #13#10#13#10 + 'Left in place:' + Left;
  Log(Summary);
  if not UninstallSilent() then
    MsgBox(Summary, mbInformation, MB_OK);
end;
