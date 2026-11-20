# 설치 파일(setup.exe) 만드는 법

ORCAdesk를 친구에게 줄 수 있는 설치 마법사(`ORCAdesk-<버전>-Setup.exe`)로
만드는 전체 과정입니다. 순서대로 따라 하세요.

순서는 반드시 이렇습니다:
  앱 빌드(dist 폴더 생성)  →  Inno Setup 설치  →  설치 파일 컴파일

---

## 가장 쉬운 방법 — 두 단계 (추천)

Inno Setup만 미리 설치해 두면(아래 2단계 참고), 그 다음부터는
**`build.bat` 실행 → `installer.iss` 컴파일** 두 단계로 설치파일까지
끝납니다.

  1. (한 번만) Python 설치, Inno Setup 설치
  2. `build.bat` 실행 → 끝나면 `installer.iss`를 더블클릭(Inno Setup)
  3. 끝나면 `installer_output\ORCAdesk-<버전>-Setup.exe` 완성
     (`installer.iss`의 `OutputBaseFilename`이 버전을 이름에 넣습니다)

자동으로 안 되거나 무슨 일이 일어나는지 알고 싶으면 아래 수동 단계를
참고하세요.

---

## 수동으로 단계별 진행

### 1단계 — 앱 빌드 (PyInstaller)

먼저 실행 파일을 만들어야 합니다. 프로젝트 폴더(`main.py`가 있는 곳)에서
명령창(cmd)을 열고:

```
build.bat
```

또는 직접:

```
python -m PyInstaller build.spec --noconfirm
```

끝나면 `dist\ORCAdesk\` 폴더가 생기고 그 안에 `ORCAdesk.exe`가
있습니다. 한 번 더블클릭해서 창이 잘 뜨는지 확인하세요. (이게 안 되면
설치 파일을 만들어도 똑같이 안 됩니다.)

---

### 2단계 — Inno Setup 설치

설치 마법사를 만들어 주는 무료 프로그램입니다.

1. https://jrsoftware.org/isdl.php 에서 Inno Setup 다운로드
2. 설치 (기본값으로 진행)

---

### 3단계 — 설치 파일 컴파일

방법 A (간단):
1. 프로젝트 폴더의 `installer.iss` 파일을 더블클릭 → Inno Setup이 열림
2. 상단 메뉴에서 **Build → Compile** (또는 F9)
3. 완료되면 `installer_output\ORCAdesk-<버전>-Setup.exe` 가 생성됩니다

방법 B (명령창) — 경로는 설치한 Inno Setup 메이저 버전에 따라 다릅니다:
```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
"C:\Program Files\Inno Setup 7\ISCC.exe" installer.iss
```

---

## 완성

`installer_output\ORCAdesk-<버전>-Setup.exe` 이 파일 하나가 설치 마법사입니다.

친구에게는 이 `ORCAdesk-<버전>-Setup.exe` 하나만 주면 됩니다.
친구는 더블클릭 → 다음 → 설치 → 시작 메뉴에서 실행.

주의사항:
- 친구도 ORCA는 따로 설치해야 합니다. 프로그램 첫 실행 후 Settings 탭에서
  orca.exe 경로를 지정하면 됩니다.
- 설치 파일은 큽니다(브라우저 엔진 포함, 100~200MB대). 정상입니다.
- 서명되지 않은 프로그램이라, 친구 PC에서 Windows가 "알 수 없는 게시자"
  경고를 띄울 수 있습니다. "추가 정보 → 실행"을 누르면 됩니다.
- **새 버전을 낼 때는 `installer.iss`의 버전을 먼저 손으로 올리세요.**
  Inno Setup은 파이썬을 읽지 못하므로 `orcamgr/paths.py`의 `APP_VERSION`을
  베껴 둔 곳이 `installer.iss` 상단의 `MyAppVersion` / `MyAppVersionFull`
  두 줄입니다. 이 값이 설치 마법사 화면, 제어판의 "프로그램 추가/제거"
  항목, 그리고 출력 파일 이름을 모두 결정합니다. `installer.iss`는
  저장소에 올라가지 않는 로컬 파일이라(.gitignore) 저장소 쪽 검사로는
  이 어긋남을 잡을 수 없으니, 릴리스 때 이 문서의 이 줄을 확인하세요.
  (실제로 0.5.0에 멈춘 채 세 번의 릴리스를 지나간 적이 있습니다.)
- 앱을 수정해서 새 버전을 배포할 때는 1~3단계를 다시 하면 됩니다.
  설정/계산 폴더는 사용자 폴더(%APPDATA%\ORCAdesk)에 따로 저장되므로
  재설치해도 보존됩니다.
