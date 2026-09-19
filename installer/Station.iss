; Compile through build_installer.ps1; AppId remains stable across all versions.
#ifndef AppVersion
  #error AppVersion must be supplied by build_installer.ps1
#endif
#ifndef WindowsVersion
  #error WindowsVersion must be supplied by build_installer.ps1
#endif
[Setup]
AppId={{95B6AE4D-9C5A-4A23-BD31-B076AF7D2358}
AppName=Local Agent AI Station
AppVersion={#AppVersion}
AppPublisher=Wave-is and contributors
AppPublisherURL=https://github.com/Wave-is/laas
AppSupportURL=https://github.com/Wave-is/laas/issues
AppUpdatesURL=https://github.com/Wave-is/laas/releases
VersionInfoVersion={#WindowsVersion}
VersionInfoProductVersion={#WindowsVersion}
VersionInfoProductTextVersion={#AppVersion}
DefaultDirName={autopf}\Local Agent AI Station
DefaultGroupName=Local Agent AI Station
; Program Files by default; "only for me" remains available in the dialog and via /CURRENTUSER.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
UsedUserAreasWarning=no
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
ShowLanguageDialog=yes
UsePreviousLanguage=no
UninstallDisplayIcon={app}\LocalAgentAIStation.exe
SetupIconFile=..\assets\brand\station.ico
OutputDir=..\dist\release
OutputBaseFilename=LocalAgentAIStation-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Global\LocalAgentAIStation.SetupGuard,Local\LocalAgentAIStation.SetupGuard
SetupMutex=Local\LocalAgentAIStation.Installer
CloseApplications=yes
RestartApplications=no
UninstallLogging=yes
LicenseFile=..\LICENSE

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "welcome.en.txt"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"; InfoBeforeFile: "welcome.ru.txt"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"; InfoBeforeFile: "welcome.uk.txt"

[CustomMessages]
en.GpuHelperTask=GPU mode switching service (WDDM/TCC without an administrator prompt)
ru.GpuHelperTask=Служба переключения режимов GPU (WDDM/TCC без запроса прав администратора)
uk.GpuHelperTask=Служба перемикання режимів GPU (WDDM/TCC без запиту прав адміністратора)
en.ServicesGroup=Services:
ru.ServicesGroup=Службы:
uk.ServicesGroup=Служби:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "gpuhelper"; Description: "{cm:GpuHelperTask}"; GroupDescription: "{cm:ServicesGroup}"; Flags: unchecked; Check: IsAdminInstallMode

[Files]
Source: "..\dist\LocalAgentAIStation.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"
Source: "..\README*.md"; DestDir: "{app}\docs"
Source: "..\docs\GETTING_STARTED*.md"; DestDir: "{app}\docs"
Source: "..\docs\THIRD_PARTY.md"; DestDir: "{app}\docs"
Source: "..\dist\licenses\*"; DestDir: "{app}\licenses"; Flags: recursesubdirs createallsubdirs
Source: "..\dist\third-party-source\*"; DestDir: "{app}\third-party-source"; Flags: recursesubdirs createallsubdirs
Source: "..\dist\dependency-versions.json"; DestDir: "{app}"
Source: "..\dist\build-requirements.lock.txt"; DestDir: "{app}"
Source: "..\dist\BUILD.json"; DestDir: "{app}"
; Bundled model engine (llama.cpp + llama-swap), staged by build_installer.ps1 -EngineDir.
Source: "..\dist\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\src\services\LocalAgentGpuModeHelper.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\src\services\install_helper.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\src\services\uninstall_helper.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Local Agent AI Station"; Filename: "{app}\LocalAgentAIStation.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Local Agent AI Station"; Filename: "{app}\LocalAgentAIStation.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\install_helper.ps1"" -InstallPath ""{app}"""; Flags: runhidden waituntilterminated; Tasks: gpuhelper; Check: NeedsGpuHelperFreshInstall; StatusMsg: "GPU helper service..."
Filename: "{app}\LocalAgentAIStation.exe"; Description: "{cm:LaunchProgram,Local Agent AI Station}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\uninstall_helper.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveGpuHelper"; Check: IsAdminInstallMode

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{95B6AE4D-9C5A-4A23-BD31-B076AF7D2358}_is1';

var
  StartupLinkExisted: Boolean;
  GpuServiceExisted: Boolean;

function StartupLinkPath(): String;
begin
  Result := ExpandConstant('{userstartup}\Local Agent AI Station.lnk');
end;

function IsGpuHelperInstalled(): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\sc.exe'), 'query LocalAgentGpuModeHelper', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function NeedsGpuHelperFreshInstall(): Boolean;
begin
  Result := IsAdminInstallMode and WizardIsTaskSelected('gpuhelper') and (not GpuServiceExisted);
end;

// Earlier versions installed per user into %LOCALAPPDATA%\Programs. When installing for all users,
// remove that copy first (user data in %LOCALAPPDATA%\LocalAgentAIStation is never touched).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Uninstaller: String;
  Code: Integer;
begin
  Result := '';
  StartupLinkExisted := FileExists(StartupLinkPath());

  // 1. Terminate running Station processes so files in {app} are unlocked for upgrade
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM LocalAgentAIStation.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM LocalAgentAIStation.exe /IM llama-server.exe /IM llama-swap.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(300);

  // 2. If the GPU helper service is already installed, stop it so its EXE in {app} is unlocked.
  // It will be restarted in CurStepChanged after files are updated without deleting the service.
  GpuServiceExisted := False;
  if IsAdminInstallMode and IsGpuHelperInstalled() then begin
    GpuServiceExisted := True;
    Exec(ExpandConstant('{sys}\sc.exe'), 'stop LocalAgentGpuModeHelper', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(1000);
  end;

  if IsAdminInstallMode and not RegQueryStringValue(HKCU, UninstallKey, 'UninstallString', Uninstaller) then
    Uninstaller := ExpandConstant('{localappdata}\Programs\Local Agent AI Station\unins000.exe');
  if IsAdminInstallMode and FileExists(RemoveQuotes(Uninstaller)) then begin
    Uninstaller := RemoveQuotes(Uninstaller);
    Log('Removing previous per-user installation: ' + Uninstaller);
    if not Exec(Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      Result := 'Could not remove the previous per-user installation (%LOCALAPPDATA%\Programs). Uninstall it in Windows Settings > Apps and retry.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    // Restart the GPU helper service if it was previously installed and running
    if IsAdminInstallMode and GpuServiceExisted then
      Exec(ExpandConstant('{sys}\sc.exe'), 'start LocalAgentGpuModeHelper', '', SW_HIDE, ewWaitUntilTerminated, Code);

    // Keep "start with Windows": recreate a removed shortcut or point an old one at this copy.
    if StartupLinkExisted then
      CreateShellLink(StartupLinkPath(), 'Local Agent AI Station '#$2014' Windows startup', ExpandConstant('{app}\LocalAgentAIStation.exe'),
        '--data-dir "' + ExpandConstant('{localappdata}\LocalAgentAIStation') + '" --startup',
        ExpandConstant('{app}'), ExpandConstant('{app}\LocalAgentAIStation.exe'), 0, SW_SHOWNORMAL);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  LinkPath: String;
  Shell, Link: Variant;
begin
  if CurUninstallStep = usUninstall then begin
    LinkPath := ExpandConstant('{userstartup}\Local Agent AI Station.lnk');
    if FileExists(LinkPath) then begin
      try
        Shell := CreateOleObject('WScript.Shell');
        Link := Shell.CreateShortcut(LinkPath);
        if CompareText(Link.TargetPath, ExpandConstant('{app}\LocalAgentAIStation.exe')) = 0 then begin
          if not DeleteFile(LinkPath) then
            Log('Could not remove the application Startup shortcut');
        end;
      except
        Log('Startup shortcut inspection failed; unrelated shortcuts are preserved');
      end;
    end;
  end;
end;
