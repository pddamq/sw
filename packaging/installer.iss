#include "..\build\prerequisites\webview2.iss"
#define AppName "Forma CAD"
#define AppVersion "1.0.0"
#ifdef FullBuild
  #define OutputName "FormaCAD-Full-Preview-Setup-" + AppVersion + "-x64"
#else
#ifdef BasicBuild
  #define OutputName "FormaCAD-Basic-Preview-Setup-" + AppVersion + "-x64"
#else
#ifdef PreviewBuild
  #define OutputName "FormaCAD-Preview-Setup-" + AppVersion + "-x64"
#else
  #define OutputName "FormaCAD-Setup-" + AppVersion + "-x64"
#endif
#endif
#endif

[Setup]
AppId={{1FD59977-34D1-4D5B-85CC-34AA95366C61}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Forma CAD
DefaultDirName={localappdata}\Programs\FormaCAD
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
OutputDir=..\dist\installer
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\FormaCAD.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Files]
Source: "..\dist\FormaCAD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\resources\prerequisites\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{group}\Forma CAD"; Filename: "{app}\FormaCAD.exe"
Name: "{group}\Forma CAD Startup Check"; Filename: "{app}\FormaCAD.exe"; Parameters: "--check-runtime"
Name: "{autodesktop}\Forma CAD"; Filename: "{app}\FormaCAD.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\FormaCAD.exe"; Description: "Launch Forma CAD"; Flags: nowait postinstall skipifsilent

[Code]
function WebViewVersionValid(Value: String): Boolean;
var
  Major, Separator, I, Dots, Digits: Integer;
begin
  Result := False;
  Dots := 0;
  Digits := 0;
  for I := 1 to Length(Value) do
  begin
    if Value[I] = '.' then
    begin
      if (Digits = 0) or (Dots >= 3) then Exit;
      Dots := Dots + 1;
      Digits := 0;
    end
    else
    begin
      if (Value[I] < '0') or (Value[I] > '9') then Exit;
      Digits := Digits + 1;
    end;
  end;
  if (Dots <> 3) or (Digits = 0) then Exit;
  Separator := Pos('.', Value);
  Major := StrToIntDef(Copy(Value, 1, Separator - 1), 0);
  Result := Major >= 86;
end;

function WebViewAt(Root: Integer): Boolean;
var
  Value: String;
begin
  Result := RegQueryStringValue(Root, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value);
  if Result then Result := WebViewVersionValid(Value);
end;

function HasWebView(): Boolean;
begin
  Result := WebViewAt(HKCU32) or WebViewAt(HKCU64) or WebViewAt(HKLM32) or WebViewAt(HKLM64);
end;

function InitializeSetup(): Boolean;
var
  NetRelease: Cardinal;
begin
  Result := True;
  if (not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', NetRelease)) or (NetRelease < 528040) then
  begin
    MsgBox('Microsoft .NET Framework 4.8 or later is required for the desktop interface. Repair it through Windows Update, then run this setup again. No .NET SDK is required.', mbInformation, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Bootstrapper: String;
  ResultCode, Attempt: Integer;
begin
  Result := '';
  if HasWebView() then Exit;
  Log('WebView2 is missing. Microsoft bootstrapper installation is required.');
  if WizardSilent() then
  begin
    if ExpandConstant('{param:INSTALLWEBVIEW2|0}') <> '1' then
    begin
      Result := 'WebView2 Runtime is missing. To permit its online installation in silent mode, supply /INSTALLWEBVIEW2=1.';
      Exit;
    end;
  end
  else if MsgBox('Microsoft Edge WebView2 Runtime is missing. Install it now from Microsoft? This requires an internet connection. No SOLIDWORKS process will be started.', mbConfirmation, MB_YESNO) <> IDYES then
  begin
    Result := 'WebView2 installation was cancelled. Install the Microsoft Evergreen Runtime and retry.';
    Exit;
  end;
  try
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    Bootstrapper := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
    if CompareText(GetSHA256OfFile(Bootstrapper), '{#WebView2Sha256}') <> 0 then
    begin
      Result := 'WebView2 bootstrapper integrity check failed. Download a fresh Forma CAD installer.';
      Exit;
    end;
    WizardForm.StatusLabel.Caption := 'Installing Microsoft Edge WebView2 Runtime...';
    if not Exec(Bootstrapper, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      Result := Format('WebView2 installer could not start (error %d). Check Windows installation permissions.', [ResultCode]);
      Exit;
    end;
    Log(Format('WebView2 bootstrapper exit code: %d', [ResultCode]));
    for Attempt := 1 to 8 do
    begin
      if HasWebView() then Exit;
      Sleep(1000);
    end;
    if (ResultCode = 3010) or (ResultCode = 1641) then
    begin
      NeedsRestart := True;
      Result := 'WebView2 installation requires a Windows restart. Restart and run setup again.';
    end
    else
      Result := Format('WebView2 is not ready (installer exit code %d). Check the network or corporate installation restrictions, then retry.', [ResultCode]);
  except
    Result := 'WebView2 preparation failed: ' + GetExceptionMessage;
  end;
end;
