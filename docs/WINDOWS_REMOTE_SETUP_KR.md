# Windows 노트북을 ORCAdesk 계산 기기로 쓰기

본체 PC의 ORCAdesk가 SSH로 Windows 노트북(또는 다른 Windows PC)에 ORCA 계산을
보내려면, 노트북에서 아래 준비를 한 번만 하면 됩니다. ORCAdesk는 SSH 설정을 직접
바꾸지 않습니다. 접속이 되면 기기 점검이 Windows인 것을 스스로 알아봅니다.

> 상태 (2026-11-23): Windows 쪽 동작은 Windows 11 PC에서 로컬로 검증했습니다.
> 실제 ORCA 6.1.1의 1코어·MS-MPI 2프로세스 계산까지 확인했습니다. 실제 Windows
> SSH 서버 너머로는 아직 확인하지 못했으니, 처음 연결할 때 함께 확인합니다.

## 1. 노트북에 준비할 것

- **Windows 10 (1809 이후) 또는 Windows 11.**
- **ORCA Windows판, 본체와 같은 버전(6.1.1).** `C:\ORCA_6.1.1`처럼 압축을 푼 폴더
  이름을 그대로 두세요. ORCAdesk가 폴더 이름에서 버전을 읽습니다. 다음 위치에
  있으면 자동으로 찾습니다.
  - PATH에 있는 `orca.exe`
  - `C:\ORCA*\`, `C:\Program Files\ORCA*\`, `%USERPROFILE%\ORCA*\`

  다른 곳에 두었다면 ORCAdesk 설정에서 경로를 지정하면 됩니다.
- **MS-MPI**(병렬 계산용): ORCA Windows판이 쓰는 Microsoft MPI를 설치합니다.
  1코어 계산만 하면 없어도 됩니다.

## 2. OpenSSH Server 켜기

**관리자 권한 PowerShell**에서 실행합니다(시작 메뉴 → PowerShell → 관리자로 실행).

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

방화벽 규칙이 켜져 있는지 확인합니다.

```powershell
Get-NetFirewallRule -Name *OpenSSH-Server* | Select-Object Name, Enabled
```

아무것도 나오지 않으면 규칙을 만듭니다.

```powershell
New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName "OpenSSH Server (sshd)" `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

## 3. 본체 PC의 키를 노트북에 등록

본체 PC 전용 공개키 한 줄(`ssh-ed25519 AAAA... orcadesk-laptop` 모양)을 노트북에
넣습니다. 키는 본체 PC에서 만들어 드립니다.

**넣는 위치는 노트북 사용자가 관리자인지에 따라 다릅니다.** 가장 흔한 실수입니다.

- **관리자 계정이면** (대부분의 개인 노트북): `C:\ProgramData\ssh\administrators_authorized_keys`
  관리자 PowerShell에서:

  ```powershell
  Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value 'ssh-ed25519 AAAA... orcadesk-laptop'
  icacls.exe C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
  ```

  권한(icacls)을 이렇게 좁히지 않으면 sshd가 파일을 무시하고 키를 거절합니다.
- **일반 계정이면**: `C:\Users\<사용자>\.ssh\authorized_keys`에 같은 한 줄을 넣습니다.

## 4. 노트북 주소

- **같은 공유기(같은 집·연구실 네트워크)**: 노트북에서 `ipconfig`를 실행해
  "IPv4 주소"(예: `192.168.0.23`)를 확인합니다. 공유기에서 이 주소를 고정해 두면
  바뀌지 않아 편합니다.
- **밖에서도 연결하려면**: Tailscale을 본체와 노트북 양쪽에 설치하고 같은 계정으로
  로그인합니다. 노트북에 `100.x.y.z` 주소(또는 기기 이름)가 생기고, 공유기 설정 없이
  어디서든 SSH가 됩니다.

## 5. 본체 PC의 `~/.ssh/config`

본체 PC `C:\Users\<본체 사용자>\.ssh\config`에 다음 항목을 추가합니다. 기기 이름은
마음대로 정해도 됩니다.

```
Host laptop
    HostName 192.168.0.23
    User <노트북 사용자 이름>
    IdentityFile ~/.ssh/orcadesk_laptop
    IdentitiesOnly yes
```

그다음 본체 PC 터미널에서 한 번 접속해 노트북을 확인합니다(`yes` 입력).

```
ssh laptop
```

비밀번호를 묻지 않고 노트북의 명령 프롬프트가 나오면 준비가 끝난 것입니다.

## 6. 계산 중에 노트북이 잠들지 않게

노트북이 절전 모드로 들어가면 계산이 멈춥니다. 전원을 연결하고, 관리자
PowerShell에서 전원 연결 시 절전·최대 절전을 끕니다.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

덮개를 닫은 채 쓰려면 제어판 → 전원 옵션 → "덮개를 닫으면" → "아무 것도 안 함"
(전원 연결 시)으로 바꿉니다.

## 7. ORCAdesk에서 켜기

본체 ORCAdesk → 설정 → **Compute devices** → **Find devices**를 누릅니다.
`laptop`에 "Windows · N cores · ORCA 6.1.1"처럼 ✓가 뜨면 **사용**을 켭니다.
필요하면 동시 작업 수와 선호 계산(SP 등)도 정합니다.

## 알아 둘 것

- **실행 폴더:** 노트북의 `%USERPROFILE%\orcadesk-runs` 아래에서 계산이 돌고, 끝나면
  폴더 전체를 본체로 가져온 뒤 노트북 쪽은 지웁니다.
- **계속 도는 범위:** ORCA는 SSH 연결과 분리된 프로세스로 띄우므로 연결이 끊기거나
  노트북 사용자가 로그아웃해도 계속 돕니다. 하지만 노트북을 **재부팅하거나 끄면
  계산도 끝납니다.**
- **백신:** 보안 프로그램이 원격으로 띄운 프로세스(WMI)를 막으면 시작이 실패합니다.
  그 경우 ORCAdesk는 그 기기를 잠시 빼고 계산을 다른 곳에서 돌립니다.
- **한글 경로:** 한글 사용자 이름이나 한글 폴더도 됩니다(로컬 검증).
- **설정을 고쳐야 하는 경우:** 노트북 IP가 바뀌면 본체 `~/.ssh/config`의 `HostName`만
  고치면 됩니다.
