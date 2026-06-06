; ============================================================
;  ORCAdesk - Inno Setup installer script
;
;  Builds ORCAdesk-Setup.exe, a standard Windows install
;  wizard (shortcuts, uninstall entry, Korean + English).
;
;  PREREQUISITE: you must first build the app with PyInstaller:
;      python -m PyInstaller build.spec --noconfirm
;  which produces  dist\ORCAdesk\ORCAdesk.exe  and its
;  runtime files. This script packages that whole folder.
;
;  HOW TO USE:
;    1. Install Inno Setup (https://jrsoftware.org/isdl.php)
;    2. Open this file in Inno Setup, or run:
;         "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;    3. The wizard is written to  installer_output\ORCAdesk-Setup.exe
; ============================================================

#define MyAppName "ORCAdesk"
#define MyAppVersion "0.1.1"
#define MyAppVersionFull "0.1.1-beta"
#define MyAppPublisher "Taewoo Kim (Korea Science Academy of KAIST)"
#define MyAppExeName "ORCAdesk.exe"

[Setup]
; A unique ID for this app (used to track installs/upgrades).
AppId={{A6F3C1D2-7E84-4B59-9C2A-1B0D7E5F4C32}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersionFull}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (c) 2026 Taewoo Kim
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; Show the MIT license and require the user to accept it.
LicenseFile=LICENSE
; Allow the user to choose the install folder.
DisableProgramGroupPage=yes
; Per-user install does not need admin rights; switch to "admin"
; + {autopf} if you prefer a machine-wide install into Program Files.
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=ORCAdesk-{#MyAppVersionFull}-Setup
; Icon shown on the Setup.exe itself and in Add/Remove Programs.
SetupIconFile=resources\orcadesk.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The app folder is large (bundled Chromium); show a real progress bar.
DiskSpanning=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Package the ENTIRE PyInstaller output folder, recursively.
; The QtWebEngine runtime lives beside the exe, so all of it must ship.
Source: "dist\ORCAdesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Also install the license text alongside the app.
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CONTRIBUTORS.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch the app at the end of installation.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
