; --- Preprocessor Variables (ISPP) ---
#define AppName "LocalReader Plus"
#define AppVersion "0.1"
#define AppPublisher "curium_rp"
#define AppExeName "start_app.vbs"
#define PowerShellExe "WindowsPowerShell\v1.0\powershell.exe"

[Setup]
; --- Application Metadata ---
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}

; --- 64-Bit Mode Configuration ---
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; --- Installation Directories ---
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes 

; --- Output Configuration ---
OutputDir=.\InnoSetupOutput
OutputBaseFilename=LocalReaderPlus_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes

; --- Aesthetics Permissions and License input---
PrivilegesRequired=admin
UninstallDisplayIcon={app}\launcher.exe
SetupIconFile=compiler:SetupClassicIcon.ico
LicenseFile=license.txt

[Dirs]
Name: "{app}"; Permissions: users-modify


; --- Files of program need to set correctly, please set path before compile program on Inno Setup compiler ---
[Files]
Source: "C:\Your path and disk\dist\*"; DestDir: "{app}"; Flags: comparetimestamp recursesubdirs createallsubdirs



[Tasks]
Name: "desktopicon"; Description: "Create {#AppName} desktop shortcut"; GroupDescription: "Desktop shortcuts:"
Name: "desktopicon_manager"; Description: "Create ONNX Engine Manager shortcut (Optional tool to swap ONNXRUNTIME CPU/GPU versions later)"; GroupDescription: "Desktop shortcuts:"; Flags: unchecked

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{group}\ONNX Engine Manager"; Filename: "{app}\engine_setup.cmd"
Name: "{autodesktop}\ONNX Engine Manager"; Filename: "{app}\engine_setup.cmd"; Tasks: desktopicon_manager
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent shellexec; Check: IsSetupSuccessful

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\webview_data"
Type: filesandordirs; Name: "{app}\userdata"
Type: filesandordirs; Name: "{app}\app"

[Code]
var
  EnginePage: TInputOptionWizardPage;
  CachePage: TInputOptionWizardPage;
  UpdatePage: TOutputMsgWizardPage;
  bIsSetupSuccessful: Boolean;

{ --- Helper Functions --- }

function IsSetupSuccessful: Boolean;
begin
  Result := bIsSetupSuccessful;
end;

function IsUpdate: Boolean;
var
  AppDir: String;
begin
  // WizardDirValue safely fetches the path without triggering the {app} initialization crash
  AppDir := WizardDirValue;
  Result := DirExists(AppDir + '\.venv') and DirExists(AppDir + '\userdata');
end;

function IsFreshInstall: Boolean;
begin
  Result := Not IsUpdate();
end;

function GetCacheFlag(Param: String): String;
begin
  if CachePage.SelectedValueIndex = 1 then
    Result := ' --no-cache'
  else
    Result := '';
end;

function GetEngineCommand(Param: String): String;
var
  CacheFlag: String;
begin
  CacheFlag := GetCacheFlag('');
  
  if EnginePage.SelectedValueIndex = 0 then 
    Result := 'Write-Host ''CPU Engine installed via requirements.'''
  else if EnginePage.SelectedValueIndex = 1 then 
    Result := 'uv pip uninstall -y onnxruntime; uv pip install onnxruntime-gpu' + CacheFlag
  else if EnginePage.SelectedValueIndex = 2 then 
    Result := 'uv pip uninstall -y onnxruntime; uv pip install "onnxruntime-gpu[cuda,cudnn]"' + CacheFlag;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  
  // If it IS an update, skip the Engine and Cache configuration pages
  if ((PageID = EnginePage.ID) or (PageID = CachePage.ID)) and IsUpdate() then
    Result := True;
    
  // If it is a FRESH install, skip the Update notification page
  if (PageID = UpdatePage.ID) and IsFreshInstall() then
    Result := True;
end;

{ --- Custom UI Summary --- }
{ Displays the user's selections on the final confirmation screen before installing }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  if IsUpdate() then
  begin
    S := 'Update Mode: Active' + NewLine + NewLine;
    S := S + 'The application core files will be updated in:' + NewLine + Space + WizardDirValue + NewLine + NewLine;
    S := S + 'Preserved data:' + NewLine + Space + '- Python Environment (.venv)' + NewLine + Space + '- User Settings (userdata)' + NewLine;
    
    // Append the shortcut creation tasks if the user selected any
    if MemoTasksInfo <> '' then
      S := S + NewLine + MemoTasksInfo;
  end
  else
  begin
    S := MemoDirInfo + NewLine + NewLine;
    
    S := S + 'Selected ONNX Engine:' + NewLine + Space;
    case EnginePage.SelectedValueIndex of
      0: S := S + 'CPU VERSION (Standard)';
      1: S := S + 'ONNX-GPU VERSION';
      2: S := S + 'ONNX-GPU [CUDA, cuDNN]';
    end;
    
    S := S + NewLine + NewLine;
    S := S + 'UV Cache Preference:' + NewLine + Space;
    if CachePage.SelectedValueIndex = 0 then
      S := S + 'Use Cache (Speeds up future installs)'
    else
      S := S + 'No Cache (Uses temporary directory)';
      
    // Append the shortcut creation tasks if the user selected any
    if MemoTasksInfo <> '' then
      S := S + NewLine + NewLine + MemoTasksInfo;
  end;
    
  Result := S;
end;

{ --- Page Initialization --- }

procedure InitializeWizard;
begin
  UpdatePage := CreateOutputMsgPage(wpWelcome,
    'Update Detected', 'Existing installation found.',
    'Setup has detected that LocalReader Plus is already installed on this system.' + #13#10#13#10 +
    'The installer will operate in Update Mode. Your existing Python environment (.venv),  and user settings will be strictly preserved.' + #13#10#13#10 +
    'Only the core application files will be refreshed. Click Next to continue.');

  EnginePage := CreateInputOptionPage(wpSelectTasks,
    'ONNX Engine Configuration', 'Automatically, download all dependencies through Terminal.',
    'Choose to download onnxruntime version:',
    True, False);

  EnginePage.Add( #13#10 +'1. CPU VERSION (Standard) ~600MB' + #13#10);
  EnginePage.Add( #13#10 + #13#10 +'2. ONNX-GPU VERSION ~800MB' + #13#10 + '   (ONNXRUNTIME-GPU only, not inculdes CUDA and cuDNN)' + #13#10 + #13#10);
  EnginePage.Add('3. ONNX-GPU [CUDA, cuDNN] ~3.25GB' + #13#10 + '    (Install CUDA and cuDNN DLLS from NVIDIA site alongside the onnxruntime-gpu package)');
  
  EnginePage.SelectedValueIndex := 0;

  CachePage := CreateInputOptionPage(EnginePage.ID,
    'This application uses the "uv" package manager to quickly set up its isolated Python environment.'  + #13#10,
    'Installation Cache Settings:',
    'Choose your download cache preference for uv:',
    True, False);

  CachePage.Add( #13#10 +  #13#10 +'1. Use Cache (Default) - Speeds up installation, when use uv next times by saving packages locally '+ #13#10+ #13#10);
  CachePage.Add( #13#10 +  #13#10 +'2. No Cache - Avoids reading or writing to the cache, uses a temporary directory'+ #13#10+ #13#10);
  
  CachePage.SelectedValueIndex := 0;
end;

{ --- Execution and Logic --- }

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    if not bIsSetupSuccessful then
    begin
      WizardForm.FinishedHeadingLabel.Caption := 'Installation Failed and Reverted';
      WizardForm.FinishedLabel.Caption := 'The setup was interrupted due to a network error or package failure.' + #13#10#13#10 + 'All extracted files and shortcuts have been safely removed from your system. You can close this window.';
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Success: Boolean;
  AppDir, EngineCmd, PSInstallUV, PSAppSetup: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Assume success by default so updates don't trigger the failure page
    bIsSetupSuccessful := True; 

    if IsFreshInstall() then
    begin
      // Require proof of success for fresh installs
      bIsSetupSuccessful := False; 
      
      WizardForm.StatusLabel.Caption := 'Setting up Python environment and downloading dependencies...';
      AppDir := ExpandConstant('{app}');
      Success := False;
      
      // Prevent PowerShell from crashing on double quotes
      EngineCmd := GetEngineCommand('');
      StringChangeEx(EngineCmd, '"', '\"', True);
      
      // PART 1: Install uv (Requires ExecutionPolicy Bypass specifically to run the .ps1 installer)
      PSInstallUV := '$ErrorActionPreference = ''Stop''; ';
      PSInstallUV := PSInstallUV + 'if (!(Get-Command uv -ErrorAction SilentlyContinue)) { Write-Host ''Downloading uv...''; Invoke-WebRequest -Uri ''https://astral.sh/uv/install.ps1'' -OutFile ''install_uv.ps1''; & .\install_uv.ps1; Remove-Item ''install_uv.ps1'' -ErrorAction SilentlyContinue }; ';
      
      // PART 2: Setup Environment (Safe execution, no ExecutionPolicy Bypass required for standard cmdlets and executables)
      PSAppSetup := '$ErrorActionPreference = ''Stop''; ';
      PSAppSetup := PSAppSetup + '$env:Path = [System.Environment]::GetEnvironmentVariable(''Path'',''Machine'') + '';'' + [System.Environment]::GetEnvironmentVariable(''Path'',''User''); ';
      PSAppSetup := PSAppSetup + 'Set-Location -LiteralPath ''' + AppDir + '''; ';
      PSAppSetup := PSAppSetup + 'if (!(Test-Path ''.venv'')) { uv venv --python 3.13; if ($LASTEXITCODE -ne 0) { exit 1 } }; ';
      PSAppSetup := PSAppSetup + 'uv pip install -r requirements.txt ' + GetCacheFlag('') + '; if ($LASTEXITCODE -ne 0) { exit 1 }; ';
      PSAppSetup := PSAppSetup + EngineCmd + '; if ($LASTEXITCODE -ne 0) { exit 1 }; ';
      PSAppSetup := PSAppSetup + 'Get-ChildItem -LiteralPath ''' + AppDir + ''' -Recurse -ErrorAction SilentlyContinue | Unblock-File; ';
      PSAppSetup := PSAppSetup + 'exit 0;';

      while not Success do
      begin
        ResultCode := 0;
        
        // Execute Part 1 (With Bypass for external script)
        Exec(ExpandConstant('{sys}\{#PowerShellExe}'), 
             '-ExecutionPolicy Bypass -Command "' + PSInstallUV + '"', 
             AppDir, SW_SHOW, ewWaitUntilTerminated, ResultCode);
             
        // Execute Part 2 (Standard secure execution) only if Part 1 succeeds
        if ResultCode = 0 then
        begin
          Exec(ExpandConstant('{sys}\{#PowerShellExe}'), 
               '-NoProfile -Command "' + PSAppSetup + '"', 
               AppDir, SW_SHOW, ewWaitUntilTerminated, ResultCode);
        end;
             
        if ResultCode = 0 then
        begin
          Success := True;
          bIsSetupSuccessful := True; 
        end
        else
        begin
          // Handles UI logic natively for retry/cancel
          if MsgBox('Installation has been interrupted (Network Error or Package Failure).' + #13#10 + #13#10 + 'Continue to try again?', mbError, MB_RETRYCANCEL) = IDCANCEL then
          begin
            // 1. Wipe Application Folder
            DelTree(AppDir, True, True, True);
            
            // 2. Wipe Ghost Shortcuts
            DeleteFile(ExpandConstant('{autodesktop}\{#AppName}.lnk'));
            DeleteFile(ExpandConstant('{group}\{#AppName}.lnk'));
            DeleteFile(ExpandConstant('{autodesktop}\ONNX Engine Manager.lnk'));
            DeleteFile(ExpandConstant('{group}\ONNX Engine Manager.lnk'));
            DeleteFile(ExpandConstant('{group}\Uninstall {#AppName}.lnk'));
            
            // 3. Break loop safely to trigger the Finish Page hijack
            Success := True; 
            bIsSetupSuccessful := False;
          end;
        end;
      end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ErrorCode: Integer;
begin
  if CurUninstallStep = usDone then
  begin
    if MsgBox('LocalReader Plus has been completely removed.' + #13#10 + #13#10 +
              'Note: This application used the "uv" package manager to handle its Python environment. ' +
              'If you do not use "uv" for any other projects, you may want to uninstall it to free up space.' + #13#10 + #13#10 +
              'Would you like to open the official documentation on how to uninstall "uv"?', 
              mbInformation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://docs.astral.sh/uv/getting-started/installation/#uninstallation', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
    end;
  end;
end;