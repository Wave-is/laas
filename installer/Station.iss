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
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Local Agent AI Station
DefaultGroupName=Local Agent AI Station
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
UninstallDisplayIcon={app}\LocalAgentAIStation.exe
SetupIconFile=..\assets\brand\station.ico
OutputDir=..\dist\release
OutputBaseFilename=LocalAgentAIStation-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\LocalAgentAIStation.SetupGuard
SetupMutex=Local\LocalAgentAIStation.Installer
CloseApplications=no
RestartApplications=no
UninstallLogging=yes
LicenseFile=..\LICENSE

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "welcome.en.txt"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"; InfoBeforeFile: "welcome.ru.txt"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"; InfoBeforeFile: "welcome.uk.txt"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

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

[Icons]
Name: "{autoprograms}\Local Agent AI Station"; Filename: "{app}\LocalAgentAIStation.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Local Agent AI Station"; Filename: "{app}\LocalAgentAIStation.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LocalAgentAIStation.exe"; Description: "{cm:LaunchProgram,Local Agent AI Station}"; Flags: nowait postinstall skipifsilent

[Code]
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
