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
; Always install per-user into LOCALAPPDATA — no UAC, no admin required, clean OTA updates.
DefaultDirName={localappdata}\Programs\Local Agent AI Station
DefaultGroupName=Local Agent AI Station
PrivilegesRequired=lowest
UsedUserAreasWarning=no
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
DisableProgramGroupPage=yes
DisableReadyPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
ShowLanguageDialog=auto
UsePreviousLanguage=yes
UninstallDisplayIcon={app}\LocalAgentAIStation.exe
SetupIconFile=..\assets\brand\station.ico
OutputDir=..\dist\release
OutputBaseFilename=LocalAgentAIStation-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupMutex=Local\LocalAgentAIStation.Installer
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
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

[Files]
Source: "..\dist\LocalAgentAIStation\*"; DestDir: "{app}"; Flags: comparetimestamp recursesubdirs createallsubdirs restartreplace
Source: "..\dist\LocalAgentAIStation\LocalAgentAIStation.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace
Source: "..\LICENSE"; DestDir: "{app}"
Source: "..\README*.md"; DestDir: "{app}\docs"
Source: "..\docs\GETTING_STARTED*.md"; DestDir: "{app}\docs"
Source: "..\docs\THIRD_PARTY.md"; DestDir: "{app}\docs"
Source: "..\dist\licenses\*"; DestDir: "{app}\licenses"; Flags: comparetimestamp recursesubdirs createallsubdirs
Source: "..\dist\third-party-source\*"; DestDir: "{app}\third-party-source"; Flags: comparetimestamp recursesubdirs createallsubdirs
Source: "..\dist\dependency-versions.json"; DestDir: "{app}"
Source: "..\dist\build-requirements.lock.txt"; DestDir: "{app}"
Source: "..\dist\BUILD.json"; DestDir: "{app}"; Flags: ignoreversion
; Bundled model engine (llama.cpp + llama-swap), staged by build_installer.ps1 -EngineDir.
Source: "..\dist\engine\*"; DestDir: "{app}\engine"; Flags: comparetimestamp recursesubdirs createallsubdirs skipifsourcedoesntexist restartreplace
; GPU mode helper service files — copied to {app}\tools\ but NOT auto-installed.
; User installs the service via Station Settings (one-time UAC prompt).
; restartreplace lets Windows update the EXE while service is stopped/restarted.
Source: "..\src\services\LocalAgentGpuModeHelper.exe"; DestDir: "{app}\tools"; Flags: comparetimestamp restartreplace
Source: "..\src\services\install_helper.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\src\services\uninstall_helper.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Local Agent AI Station"; Filename: "{app}\LocalAgentAIStation.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Local Agent AI Station"; Filename: "{app}\LocalAgentAIStation.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LocalAgentAIStation.exe"; Description: "{cm:LaunchProgram,Local Agent AI Station}"; Flags: nowait postinstall skipifsilent

[Code]
var
  StartupLinkExisted: Boolean;

function StartupLinkPath(): String;
begin
  Result := ExpandConstant('{userstartup}\Local Agent AI Station.lnk');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  StartupLinkExisted := FileExists(StartupLinkPath());
  // Force-terminate running Station and engine instances so files are fully unlocked and VRAM is freed.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM LocalAgentAIStation.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM llama-server.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM llama-swap.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    // Keep "start with Windows": recreate a removed shortcut or point an old one at this copy.
    if StartupLinkExisted then
      CreateShellLink(StartupLinkPath(), 'Local Agent AI Station — Windows startup', ExpandConstant('{app}\LocalAgentAIStation.exe'),
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
