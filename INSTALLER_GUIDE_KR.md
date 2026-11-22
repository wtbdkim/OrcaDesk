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

`build.bat`은 매번 깨끗한 가상환경 `.venv-build\`(git에 올라가지 않음)를
새로 만들고, `requirements-build.txt`에 고정된 버전만 설치한 뒤 빌드합니다.
Python은 3.12(가능하면 3.12.10)가 필요합니다.

직접 하려면, `.venv-build`를 만든 다음 그 안의 Python으로:

```
.venv-build\Scripts\python -m PyInstaller build.spec --noconfirm
```

**전역 Python(`python -m PyInstaller ...`)으로 빌드하지 마세요.** PyInstaller는
빌드하는 Python이 import할 수 있는 것을 모두 묶기 때문에, 이 PC에 깔린 다른
패키지(PyYAML 등)나 PATH에 있는 DLL(Git for Windows의 OpenSSL 등)이 섞여
들어가고, 그러면 `THIRD_PARTY_NOTICES.txt`가 실제 묶인 내용과 달라집니다.

끝나면 `dist\ORCAdesk\` 폴더가 생기고 그 안에 `ORCAdesk.exe`와 함께
`LICENSE`, `LICENSE-BINARY.txt`, `THIRD_PARTY_NOTICES.txt`, `licenses\` 폴더가
있습니다(라이선스 고지 — 아래 "라이선스" 참고). 한 번 더블클릭해서 창이 잘
뜨는지 확인하세요. (이게 안 되면 설치 파일을 만들어도 똑같이 안 됩니다.)

> 이전 빌드의 `dist\` 안에 읽기 전용 폴더가 남아 있으면 PyInstaller가
> `WinError 5`로 실패할 수 있습니다. 그때는 `dist\` 폴더를 지우거나 그 안
> 폴더들의 읽기 전용 속성을 해제한 뒤 다시 실행하세요.

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
  항목, 그리고 출력 파일 이름을 모두 결정합니다. (`installer.iss`가 git에서
  빠져 있던 시절에는 0.5.0에 멈춘 채 세 번의 릴리스를 지나간 적이 있습니다.)
  이제 `installer.iss`는 저장소에 있고, `tests/test_release_notices.py`가
  이 버전과 `APP_VERSION`이 다르면 실패합니다. 같은 테스트가
  `LICENSE-BINARY.txt`의 소스 태그(`.../tree/v<버전>`)와
  `THIRD_PARTY_NOTICES.txt` 첫 줄의 버전도 확인하니, 함께 올리세요.

## 라이선스 (설치 파일을 배포하기 전에)

- ORCAdesk 소스는 MIT지만, 설치 파일에는 PyQt6(GPL-3.0), Qt·Qt WebEngine
  (LGPL-3.0, 안에 Chromium), Python 등이 함께 들어가므로 **설치 파일 전체는
  GPL-3.0으로 배포**됩니다. 그래서 설치 마법사의 라이선스 화면은 MIT 전문이
  아니라 `LICENSE-BINARY.txt`를 보여 줍니다.
- `build.spec`이 `LICENSE`, `LICENSE-BINARY.txt`, `THIRD_PARTY_NOTICES.txt`,
  `licenses\`를 `dist\ORCAdesk\`의 `ORCAdesk.exe` 옆에 복사하고, 설치 파일이
  이것들을 그대로 설치 폴더에 넣습니다. `dist\ORCAdesk`에 이 파일들이 없으면
  `installer.iss`는 컴파일을 거부합니다(예전 빌드로 설치 파일을 만드는 것을 막음).
- `requirements-build.txt`의 버전이나 Python 버전을 바꾸면 묶이는 내용이
  바뀌므로 `THIRD_PARTY_NOTICES.txt`, `LICENSE-BINARY.txt`, `licenses\`도 같이
  고쳐야 합니다(자세한 내용: `docs/ARCHITECTURE.md`의 "Packaging the Windows
  build").
- GitHub 릴리스에 설치 파일을 올릴 때는 그 빌드의 커밋에 `v<버전>` 태그를
  붙이고, 릴리스 설명에도 `LICENSE-BINARY.txt`의 "Corresponding source" 내용을
  적어 두세요.
- 앱을 수정해서 새 버전을 배포할 때는 1~3단계를 다시 하면 됩니다.
  설정/계산 폴더는 사용자 폴더(%APPDATA%\ORCAdesk)에 따로 저장되므로
  재설치해도 보존됩니다.
