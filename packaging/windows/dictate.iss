[Setup]
AppId={{5D17F278-A6CC-4CC5-B67D-6122130B31A8}
AppName=Dictate
AppVersion={#Version}
AppPublisher=KAGG Design
AppPublisherURL=https://github.com/kagg-design/dictate
DefaultDirName={localappdata}\Programs\Dictate
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
AppMutex=Local\KAGG.Dictate.Installed
CloseApplications=no
SetupIconFile={#PayloadDir}\icon.ico
UninstallDisplayIcon={app}\icon.ico
OutputDir={#OutputPath}
OutputBaseFilename=Dictate-Setup-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "cuda"; Description: "Download NVIDIA libraries for GPU acceleration (about 1.3 GB)"; Check: HasNvidiaDriver

[Icons]
Name: "{autoprograms}\Dictate"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-E -s -B -m src.main --show"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "KAGG.Dictate"
Name: "{autodesktop}\Dictate"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-E -s -B -m src.main --show"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "KAGG.Dictate"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\KAGG.Dictate"; ValueType: string; ValueName: "DisplayName"; ValueData: "Dictate"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\KAGG.Dictate"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\icon.ico"
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\KAGG.Dictate"; ValueType: dword; ValueName: "ShowInSettings"; ValueData: "1"

[InstallDelete]
Type: filesandordirs; Name: "{app}\runtime"; Check: HasExistingInstallation

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-E -s -B -m src.main --show"; WorkingDir: "{app}"; Description: "Open Dictate"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{userstartup}\KAGG Dictate.lnk"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\cuda"

[Code]
var
  CpuPage: TOutputMsgWizardPage;

function HasExistingInstallation: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\dictate-installed.txt'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not HasExistingInstallation and (DirExists(ExpandConstant('{app}\runtime')) or
    DirExists(ExpandConstant('{app}\cuda'))) then
    Result := 'This folder is already used by another program. Choose a separate installation folder for Dictate.';
end;

function HasNvidiaDriver: Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\nvcuda.dll'));
end;

function HasCudaRuntime: Boolean;
var
  CudaPath: String;
begin
  CudaPath := ExpandConstant('{app}\cuda');
  Result := FileExists(CudaPath + '\nvidia\cudnn\bin\cudnn64_9.dll') and
    FileExists(CudaPath + '\nvidia\cublas\bin\cublas64_12.dll') and
    FileExists(CudaPath + '\nvidia\cuda_nvrtc\bin\nvrtc64_120_0.dll');
end;

procedure InitializeWizard;
begin
  CpuPage := CreateOutputMsgPage(wpSelectTasks, 'CPU mode', 'NVIDIA acceleration is unavailable with these settings',
    'Dictate can use Whisper on your processor. Transcription may take several seconds after you release the hotkey, ' +
    'especially for longer dictation. A compatible NVIDIA GPU is recommended for fast transcription.' + #13#10#13#10 +
    'Choose Next to install in CPU mode, or Cancel to exit Setup.');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = CpuPage.ID then
    Result := HasNvidiaDriver and (WizardIsTaskSelected('cuda') or HasCudaRuntime);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  Command, CudaPath, Requirements, CacheKey: String;
  PreviousKey: AnsiString;
begin
  if (CurStep <> ssPostInstall) or not WizardIsTaskSelected('cuda') then
    exit;

  CudaPath := ExpandConstant('{app}\cuda');
  Requirements := ExpandConstant('{app}\requirements-cuda.txt');
  CacheKey := GetSHA256OfFile(Requirements);
  if LoadStringFromFile(CudaPath + '\requirements.sha256', PreviousKey) and
    (PreviousKey = CacheKey) and HasCudaRuntime then
    exit;

  WizardForm.StatusLabel.Caption := 'Downloading and installing NVIDIA libraries...';
  Command := '-I -m pip --isolated install --disable-pip-version-check --no-cache-dir ' +
    '--only-binary=:all: --no-deps --require-hashes --upgrade --target ' + AddQuotes(CudaPath) +
    ' -r ' + AddQuotes(Requirements);
  if ExecAndLogOutput(ExpandConstant('{app}\runtime\python.exe'), Command,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode, nil) and (ExitCode = 0) then
    SaveStringToFile(CudaPath + '\requirements.sha256', CacheKey, False)
  else
  begin
    Log('NVIDIA runtime installation failed, exit code ' + IntToStr(ExitCode));
    SuppressibleMsgBox('The NVIDIA libraries could not be installed. You can use Dictate with the CPU ' +
      'or run Setup again to retry the download.', mbError, MB_OK, IDOK);
  end;
end;
