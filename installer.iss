; ============================================================
;  ORCAdesk - Inno Setup installer script
;
;  Builds ORCAdesk-<version>-Setup.exe, a standard Windows install
;  wizard (shortcuts, uninstall entry, Korean + English).
;
;  This file is tracked in git: together with build.spec,
;  build.bat and requirements-build.txt it is part of the
;  installer's corresponding source (the installer is conveyed
;  under the GPL-3.0 as a whole; see LICENSE-BINARY.txt).
;
;  PREREQUISITE: you must first build the app:
;      build.bat
;  (a clean virtual environment, then PyInstaller build.spec)
;  which produces  dist\ORCAdesk\ORCAdesk.exe, its runtime files
;  and the licence notices next to the exe. This script packages
;  that whole folder, and refuses to compile without the notices.
;
;  HOW TO USE:
;    1. Install Inno Setup (https://jrsoftware.org/isdl.php)
;    2. Open this file in Inno Setup, or run:
;       (the path depends on the major version installed)
;         "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;         "C:\Program Files\Inno Setup 7\ISCC.exe" installer.iss
;    3. The wizard is written to
;         installer_output\ORCAdesk-<version>-Setup.exe
; ============================================================

#define MyAppName "ORCAdesk"
; Keep these two in sync with APP_VERSION (orcamgr/paths.py) at every release
; — Inno Setup can't read Python, so this is the one hand-synced version copy.
#define MyAppVersion "0.12.0"
#define MyAppVersionFull "0.12.0-beta"
#define MyAppPublisher "Taewoo Kim"
#define MyAppURL "https://github.com/wtbdkim/OrcaDesk"
#define MyAppExeName "ORCAdesk.exe"

; The licence notices build.spec copies next to ORCAdesk.exe. An installer
; without them would convey PyQt6 / Qt / Chromium without their licences, so
; a dist\ folder that lacks them (an old build) stops the compile here.
#define DistDir AddBackslash(SourcePath) + "dist\ORCAdesk\"
#if !FileExists(DistDir + "THIRD_PARTY_NOTICES.txt") || !FileExists(DistDir + "LICENSE-BINARY.txt") || !FileExists(DistDir + "LICENSE") || !FileExists(DistDir + "licenses\GPL-3.0.txt")
  #error dist\ORCAdesk lacks the licence notices - rebuild with build.bat
#endif

[Setup]
; A unique ID for this app (used to track installs/upgrades).
AppId={{A6F3C1D2-7E84-4B59-9C2A-1B0D7E5F4C32}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersionFull}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppCopyright=Copyright (c) 2026 Taewoo Kim
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; The licence page: what this binary is licensed under. ORCAdesk's own code
; is MIT (LICENSE); the installer bundles PyQt6 (GPL-3.0), Qt (LGPL-3.0) and
; Chromium among others, so as a whole it is conveyed under the GPL-3.0.
; LICENSE-BINARY.txt says so and points at THIRD_PARTY_NOTICES.txt, the
; licenses\ folder and the corresponding source.
LicenseFile=LICENSE-BINARY.txt
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
; It also carries the licence notices build.spec put next to the exe
; (checked above): LICENSE (MIT, ORCAdesk's own code), LICENSE-BINARY.txt,
; THIRD_PARTY_NOTICES.txt and licenses\ - all installed into {app}.
Source: "dist\ORCAdesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
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
