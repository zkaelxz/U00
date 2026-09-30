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
; Setup exits with code 8 if the files were copied but the Python packages
; didn't install (details in <data>\launcher\install.log).
; Silent uninstall ({app}\unins000.exe /VERYSILENT) keeps all user data.

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

[UninstallDelete]
; pip-installed packages and compiled .pyc files aren't in Inno's own file
; list, so the program folders are removed explicitly. Never the data folder.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty; Name: "{app}"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  PostInstallFailed: Boolean;
  UninstDataDir: String;
  DeleteLibrary, DeleteSettings, DeleteModels: Boolean;

function DefaultDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#AppName}');
end;

function NormDir(const Dir: String): String;
begin
  Result := Lowercase(AddBackslash(RemoveBackslashUnlessRoot(Trim(Dir))));
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
  else if (AppDir <> '') and (Pos(NormDir(AppDir), NormDir(D)) = 1) then
    Result := 'Your data folder can''t be inside the install folder (' + AppDir +
      '): updates and uninstalling replace that folder.';
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
  Problem: String;
begin
  Result := True;
  if CurPageID = DataDirPage.ID then
  begin
    Problem := DataDirProblem(DataDir(), WizardDirValue());
    if Problem <> '' then
    begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
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

// Stops a server the previous install's launcher started, so its files
// can be replaced. Harmless when nothing is running or installed.
procedure StopRunningServer(const AppDir: String);
var
  ResultCode: Integer;
begin
  if FileExists(AppDir + '\python\python.exe') and FileExists(AppDir + '\app\installer\launcher.py') then
    Exec(AppDir + '\python\python.exe', '-s "' + AppDir + '\app\installer\launcher.py" --stop',
      AppDir + '\app', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  // Checked here too: NextButtonClick doesn't run for a silent install.
  Result := DataDirProblem(DataDir(), ExpandConstant('{app}'));
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
  if PostInstallFailed then
    Result := 8
  else
    Result := 0;
end;

// ---------------- Uninstall ----------------

// The data folder this install used: the first non-blank, non-comment line
// of app\INSTALLED in the install folder (written by postinstall.py). '' if
// unknown, in which case no user data is ever deleted.
function ReadInstalledDataDir(): String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line: String;
begin
  Result := '';
  if LoadStringsFromFile(ExpandConstant('{app}\app\INSTALLED'), Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      Line := Trim(Lines[I]);
      if (Line <> '') and (Line[1] <> '#') then
      begin
        Result := RemoveBackslashUnlessRoot(Line);
        Break;
      end;
    end;
  if (Result <> '') and (DataDirProblem(Result, ExpandConstant('{app}')) <> '') then
    Result := '';
end;

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

// Asks what, beyond the program, to delete. Every box starts unticked.
// Returns False if the user cancels the uninstall.
function AskWhatToDelete(): Boolean;
var
  Form: TSetupForm;
  Intro: TNewStaticText;
  LibraryBox, SettingsBox, ModelsBox: TNewCheckBox;
  OkButton, CancelButton: TNewButton;
begin
  Form := CreateCustomForm(ScaleX(480), ScaleY(250), False, False);
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

    Result := Form.ShowModal() = mrOk;
    if Result then
    begin
      DeleteLibrary := LibraryBox.Checked;
      DeleteSettings := SettingsBox.Checked;
      DeleteModels := ModelsBox.Checked;
    end;
  finally
    Form.Free();
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  DeleteLibrary := False;
  DeleteSettings := False;
  DeleteModels := False;
  UninstDataDir := ReadInstalledDataDir();
  // A silent uninstall never deletes user data.
  if (not UninstallSilent()) and (UninstDataDir <> '') and DirExists(UninstDataDir) then
    Result := AskWhatToDelete();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep <> usPostUninstall) or (UninstDataDir = '') then
    Exit;
  // Only these named items, never the folder's other contents: the folder
  // may be one the user picked and shares with other files.
  if DeleteLibrary then
    DelTree(UninstDataDir + '\library', True, True, True);
  if DeleteSettings then
    DeleteFile(UninstDataDir + '\.env');
  if DeleteModels then
    DelTree(UninstDataDir + '\model_cache', True, True, True);
  // The launcher's pid file and logs: not user data.
  DelTree(UninstDataDir + '\launcher', True, True, True);
  // Removed only if nothing else is left in it.
  RemoveDir(UninstDataDir);
end;
