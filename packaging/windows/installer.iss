; Inno Setup script for Simple-Ripper.
; Built with: iscc packaging\windows\installer.iss
; Expects dist\Simple-Ripper\ (PyInstaller onedir output) to already
; exist, and the version passed in via /DAppVersion=x.y.z (the release
; workflow does this so the .iss file never has to be hand-edited per
; release).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Simple-Ripper"
#define RepoRoot AddBackslash(SourcePath) + "..\.."

[Setup]
AppId={{DDB04440-83E1-4DAB-ADFA-0C53A5EA6B33}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Mike Hellyer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\Simple-Ripper.exe
OutputDir={#RepoRoot}\dist
OutputBaseFilename=Simple-Ripper-{#AppVersion}-Windows-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#RepoRoot}\packaging\icons\icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#RepoRoot}\dist\Simple-Ripper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Simple-Ripper.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Simple-Ripper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Simple-Ripper.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
