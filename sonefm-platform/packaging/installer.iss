; SoneFM 업무자동화 설치 파일 (Inno Setup 6). 한글이 있으니 UTF-8 BOM으로 저장한다.
; 먼저: .venv\Scripts\pyinstaller packaging\platform.spec --noconfirm
; 그다음: ISCC packaging\installer.iss  → packaging\Output\SoneFM-Platform-Setup-<버전>.exe
; AppId 고정: 새 버전을 덮어 설치하면 업데이트된다. 데이터(문서\SoneFM\platform)는 지우지 않는다.
#define AppName "SoneFM 업무자동화"
#define AppVersion "1.0.0"
#define AppExe "SoneFMPlatform.exe"

[Setup]
AppId={{9C4E1D2B-7A3F-4E6B-8D15-3B2F6A9C0E71}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SoneFM 수원공동체라디오
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=SoneFM-Platform-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 아이콘 만들기"; GroupDescription: "추가 작업:"

[Files]
Source: "..\dist\SoneFMPlatform\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{#AppName} 실행"; Flags: nowait postinstall skipifsilent
