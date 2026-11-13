; Clip Genie Setup.exe (Inno Setup 6). Build:
;   "K:\ClipGenieBuild\InnoSetup6\ISCC.exe" /DSrc="<staged ClipGenie folder>" /O"<out dir>" tools\installer\clipgenie.iss
; <staged ClipGenie folder> = the "ClipGenie" folder tools/build_tester.py makes (app, engine, python, ffmpeg, README.md).
; Installs for this Windows user only (no admin), to the same place as before:
; %LOCALAPPDATA%\GenieStudioFX\ClipGenie by default; the user can pick another folder (the launcher only stops the
; engine of its own folder, so any path works).

#ifndef Src
  #error Pass /DSrc="...\ClipGenie"
#endif
;   the version comes from PUBLIC_VERSION in engine\clipgenie\server.py (the one the app shows); /DAppVer=x.y.z overrides
#ifndef AppVer
  #define VerFile AddBackslash(SourcePath) + "..\..\engine\clipgenie\server.py"
  #define AppVer ""
  #define FH FileOpen(VerFile)
  #if !FH
    #error Can't read engine\clipgenie\server.py for the version: pass /DAppVer=x.y.z
  #endif
  #sub ReadVer
    #define Line FileRead(FH)
    #if Pos('PUBLIC_VERSION = "', Line) == 1
      #define public AppVer Copy(Line, 19, Pos('"', Copy(Line, 19)) - 1)
    #endif
  #endsub
  #for {0; AppVer == "" && !FileEof(FH); 0} ReadVer
  #expr FileClose(FH)
  #if AppVer == ""
    #error No PUBLIC_VERSION in engine\clipgenie\server.py: pass /DAppVer=x.y.z
  #endif
#endif
#ifndef Stage
  ; the creator (12 Nov): installs say Beta. /DStage= (empty) for a final release
  #define Stage "Beta"
#endif
#if Stage == ""
  #define Label AppVer
  #define BetaNote ""
#else
  #define Label AppVer + " " + Stage
  #define BetaNote "This is a beta: things can still break. Please tell us when they do.%n%n"
#endif

[Setup]
AppId={{7C1F2B9A-4E3D-4C8B-9B2A-C11E6E9A3D57}
AppName=Clip Genie
AppVersion={#Label}
AppVerName=Clip Genie {#Label}
AppPublisher=GenieStudioFX
AppCopyright=Copyright (C) 2026 GenieStudioFX
VersionInfoVersion={#AppVer}
VersionInfoCompany=GenieStudioFX
VersionInfoDescription=Clip Genie {#Stage} Setup
VersionInfoProductName=Clip Genie
VersionInfoProductVersion={#AppVer}
PrivilegesRequired=lowest
DefaultDirName={code:DefDir}
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=no
UsePreviousAppDir=yes
AppendDefaultDirName=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=Clip Genie Setup {#Label}
SetupIconFile=..\..\app\icon.ico
UninstallDisplayIcon={app}\app\icon.ico
UninstallDisplayName={#Trim("Clip Genie " + Stage)}
; dark purple theme to match the app (the creator: "extra premium")
WizardStyle=modern dark hidebevels includetitlebar
WizardBackColor=#150b28
WizardSizePercent=110
WizardImageFile=wizard_1x.bmp,wizard_2x.bmp
WizardSmallImageFile=small_1x.bmp,small_2x.bmp
WizardImageStretch=yes
Compression=lzma2/normal
SolidCompression=no
LZMAUseSeparateProcess=yes
CloseApplications=no
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install Clip Genie {#Label} on your computer.%n%n{#BetaNote}Clip Genie turns your Twitch streams and clips into TikToks: webcam + gameplay, cuts, titles and subtitles.%n%nYou need about 5 GB of free space. An NVIDIA graphics card makes it much faster.
FinishedLabel=Clip Genie is installed.%n%nThe first stream takes a bit longer: it downloads the speech model once (about 0.5 GB).

[Tasks]
Name: "desktopicon"; Description: "Put Clip Genie on the Desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Src}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Src}\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__"
Source: "{#Src}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__"
Source: "{#Src}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Src}\README.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; old .cmd installs left these
Type: files; Name: "{app}\uninstall.ps1"

[Icons]
Name: "{autoprograms}\Clip Genie"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\engine\run_app.pyw"""; WorkingDir: "{app}\engine"; IconFilename: "{app}\app\icon.ico"; Comment: "Clip Genie: Twitch streams to TikToks"; Check: not IsTest
Name: "{autodesktop}\Clip Genie"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\engine\run_app.pyw"""; WorkingDir: "{app}\engine"; IconFilename: "{app}\app\icon.ico"; Comment: "Clip Genie: Twitch streams to TikToks"; Tasks: desktopicon; Check: not IsTest

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\engine\run_app.pyw"""; WorkingDir: "{app}\engine"; Description: "Open Clip Genie now"; Flags: postinstall nowait skipifsilent; Check: not IsTest

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""{code:StopCmd}"""; Flags: runhidden waituntilterminated; RunOnceId: "StopEngine"

[UninstallDelete]
; app files + what Clip Genie downloads next to them (speech models, cache, window profile).
; Settings / library in %APPDATA% and the finished videos are kept.
Type: filesandordirs; Name: "{app}"
; downloads Clip Genie keeps in AppData whatever folder it was installed to (speech models, stream cache, window)
Type: filesandordirs; Name: "{localappdata}\GenieStudioFX\ClipGenie\models"; Check: not IsTest
Type: filesandordirs; Name: "{localappdata}\GenieStudioFX\ClipGenie\cache"; Check: not IsTest
Type: filesandordirs; Name: "{localappdata}\GenieStudioFX\ClipGenie\window"; Check: not IsTest
Type: filesandordirs; Name: "{localappdata}\GenieStudioFX\ClipGenie\window2"; Check: not IsTest
Type: dirifempty; Name: "{localappdata}\GenieStudioFX\ClipGenie"; Check: not IsTest

[Code]
{ /CGTEST on the command line: no shortcuts, no settings, no start (for checking a copy in another folder with /DIR=) }
function IsTest: Boolean;
begin
  Result := Pos('/CGTEST', Uppercase(GetCmdTail)) > 0;
end;

{ test mode never points at a real install }
function DefDir(Param: String): String;
begin
  if IsTest then
    Result := ExpandConstant('{%TEMP}\ClipGenieSetupTest\ClipGenie')
  else
    Result := ExpandConstant('{localappdata}\GenieStudioFX\ClipGenie');
end;

{ stops only the Clip Genie running from THIS folder }
function StopCmdFor(Dir: String): String;
begin
  Result := 'Get-CimInstance Win32_Process -Filter ''Name=\"pythonw.exe\" OR Name=\"python.exe\"'' | ' +
            'Where-Object { $_.CommandLine -like ''*' + Dir + '\*run_*'' } | ' +
            'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }';
end;

function StopCmd(Param: String): String;
begin
  Result := StopCmdFor(ExpandConstant('{app}'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { stop a running Clip Genie so its files can be replaced (never in test mode) }
  if not IsTest then
    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' + StopCmdFor(WizardDirValue) + '"', '',
       SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir: String;
begin
  if (CurStep = ssPostInstall) and not IsTest then
  begin
    { first run: empty name tag (the welcome screen asks for the Twitch name); plain ASCII, no BOM }
    Dir := ExpandConstant('{userappdata}\GenieStudioFX\ClipGenie');
    ForceDirectories(Dir);
    if not FileExists(Dir + '\settings.json') then
      SaveStringToFile(Dir + '\settings.json', '{ "style": { "watermark": "" } }', False);
  end;
end;
