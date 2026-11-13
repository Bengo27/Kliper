; Kliper Setup.exe (Inno Setup 6). Build:
;   "K:\KliperBuild\InnoSetup6\ISCC.exe" /DSrc="<staged Kliper folder>" /O"<out dir>" tools\installer\clipgenie.iss
; <staged Kliper folder> = the "Kliper" folder tools/build_tester.py makes (app, engine, python, ffmpeg, README.md).
; Installs for this Windows user only (no admin), to the same place as before:
; %LOCALAPPDATA%\BengoGames\Kliper by default; the user can pick another folder (the launcher only stops the
; engine of its own folder, so any path works).

#ifndef Src
  #error Pass /DSrc="...\Kliper"
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
  #define BetaNote "זוהי גרסת בטא: דברים עוד יכולים להישבר. ספרו לנו כשזה קורה.%n%n"
#endif

[Setup]
AppId={{A3E5D1C2-6B7F-4E21-9C8D-5B1F0E2A7D44}
AppName=Kliper
AppVersion={#Label}
AppVerName=Kliper {#Label}
AppPublisher=Bengo
AppCopyright=Copyright (C) 2026 Bengo; based on Clip Genie (C) 2026 Genie Studio FX, GPL-3.0
VersionInfoVersion={#AppVer}
VersionInfoCompany=BengoGames
VersionInfoDescription=Kliper {#Stage} Setup
VersionInfoProductName=Kliper
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
OutputBaseFilename=Kliper Setup {#Label}
SetupIconFile=..\..\app\icon.ico
UninstallDisplayIcon={app}\app\icon.ico
UninstallDisplayName={#Trim("Kliper " + Stage)}
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
Name: "he"; MessagesFile: "compiler:Languages\Hebrew.isl"

[Messages]
WelcomeLabel2=ההתקנה תתקין את קליפר {#Label} במחשב שלך.%n%n{#BetaNote}קליפר הופך את הסטרימים שלך לקליפים לטיקטוק, שורטס ורילס: מצלמה + משחק, חיתוכים, כותרות וכתוביות בעברית.%n%nצריך בערך 6GB פנויים. כרטיס מסך של NVIDIA מאיץ מאוד.
FinishedLabel=קליפר הותקן.%n%nהסטרים הראשון לוקח קצת יותר זמן: מודל התמלול העברי יורד פעם אחת (כ-1.6GB).

[Tasks]
Name: "desktopicon"; Description: "קיצור דרך בשולחן העבודה"; GroupDescription: "קיצורי דרך:"

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
Name: "{autoprograms}\Kliper"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\engine\run_app.pyw"""; WorkingDir: "{app}\engine"; IconFilename: "{app}\app\icon.ico"; Comment: "קליפר: מסטרים לקליפים"; Check: not IsTest
Name: "{autodesktop}\Kliper"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\engine\run_app.pyw"""; WorkingDir: "{app}\engine"; IconFilename: "{app}\app\icon.ico"; Comment: "קליפר: מסטרים לקליפים"; Tasks: desktopicon; Check: not IsTest

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\engine\run_app.pyw"""; WorkingDir: "{app}\engine"; Description: "לפתוח את קליפר עכשיו"; Flags: postinstall nowait skipifsilent; Check: not IsTest

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""{code:StopCmd}"""; Flags: runhidden waituntilterminated; RunOnceId: "StopEngine"

[UninstallDelete]
; app files + what Kliper downloads next to them (speech models, cache, window profile).
; Settings / library in %APPDATA% and the finished videos are kept.
Type: filesandordirs; Name: "{app}"
; downloads Kliper keeps in AppData whatever folder it was installed to (speech models, stream cache, window)
Type: filesandordirs; Name: "{localappdata}\BengoGames\Kliper\models"; Check: not IsTest
Type: filesandordirs; Name: "{localappdata}\BengoGames\Kliper\cache"; Check: not IsTest
Type: filesandordirs; Name: "{localappdata}\BengoGames\Kliper\window"; Check: not IsTest
Type: filesandordirs; Name: "{localappdata}\BengoGames\Kliper\window2"; Check: not IsTest
Type: dirifempty; Name: "{localappdata}\BengoGames\Kliper"; Check: not IsTest

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
    Result := ExpandConstant('{%TEMP}\KliperSetupTest\Kliper')
  else
    Result := ExpandConstant('{localappdata}\BengoGames\Kliper');
end;

{ stops only the Kliper running from THIS folder }
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
  { stop a running Kliper so its files can be replaced (never in test mode) }
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
    Dir := ExpandConstant('{userappdata}\BengoGames\Kliper');
    ForceDirectories(Dir);
    if not FileExists(Dir + '\settings.json') then
      SaveStringToFile(Dir + '\settings.json', '{ "style": { "watermark": "" } }', False);
  end;
end;
