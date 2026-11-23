# 원격 계산 기기 (SSH) — 1단계 설계

상태: **1단계 구현됨, 실제 SSH 기기로는 아직 검증 안 됨** (가짜 transport 테스트만).
코드가 하는 일은 `docs/ARCHITECTURE.md` *Remote devices* 절에 있다. 실제 기기 검증이
끝나면 이 문서의 확정 내용을 그 절로 옮기고, 이 문서는 지운다.

구현과 이 문서의 차이:
- 설정 카드는 "사용 / 동시 작업 수 / 코어 / 메모리"만 화면에서 고친다. ORCA 경로와
  원격 작업 폴더는 `settings.json`의 `remote_devices`에서만 바꿀 수 있다(아직 UI 없음).
- 실행 도중 기기 한도를 바꾸면 다음 실행부터 적용된다. 실행 중 반영(`set_budget`)은
  기기별 풀에 아직 연결하지 않았다.
- 정리 목록의 기기 복귀 처리는 기기 점검(시작 시, "Find devices", 실행 중 60초 감시)이
  성공할 때 돈다. 앱 시작 직후의 첫 점검이 세션 복원보다 먼저 끝나면 그 회차는 건너뛴다.

## 0. 한 줄 요약

본체 ORCAdesk의 **하나의 대기열**이, 지금 돌릴 수 있는(의존 계산이 끝난)
ORCA 계산을 **이 PC 또는 SSH로 연결된 Linux 기기** 중 자리가 있는 곳에 자동으로
보낸다. 원격에서 끝난 계산은 **실행 폴더 전체**를 본체 작업 폴더로 가져온 뒤에야
DONE이 되고, 원격 쪽 폴더는 검증 후 지운다. 사용자에게 원격 과정은 보이지 않는다.
단 하나의 예외로, 기기와 연결이 끊기면 그 계산은 주황색 "연결 끊김" 상자로 보인다.

## 1. 결정 사항 (사용자 확정, 2026-11-23)

| # | 결정 |
|---|---|
| D1 | SSH 설정(키, `~/.ssh/config`)은 **사용자가 직접** 한다. ORCAdesk는 설정된 기기를 **인식만** 한다. |
| D2 | 기기별 탭이 아니라 **자동 분배**. 동시에 도는 슬롯(대기열의 노란 박스) 수 = 이 PC 설정 + 기기별 설정. |
| D3 | 의존 관계는 지금 규칙 그대로: 부모 결과가 없는 계산은 기다리고, 돌릴 수 있는 것만 보낸다. |
| D4 | 원격 계산이 끝나면(성공·실패·취소·멈춤 모두) **원격 실행 폴더 전체**를 본체의 그 계산 실행 폴더로 가져온다. |
| D5 | 가져온 뒤 파일 개수·크기가 모두 맞는지 **확인한 다음 원격 폴더를 삭제**한다. |
| D6 | 원격 실행 과정(업로드, 실행, 동기화)은 사용자에게 **보이지 않는다**. 새 상태도, 공유 로그 줄도 없다. 유일한 예외는 D7. |
| D7 | 기기와 **연결이 끊기면** 영향받는 계산마다 **"연결 끊김"을 주황색 상자**로 표시한다. 실행 중·대기의 호박색(`--warn`), 실패의 빨강(`--err`)과 구별되는 색이다(§8.1). |

## 2. 범위

**1단계에 포함**: ORCA 계산 종류 전부(opt, freq, irc, neb_ts, tddft, …).

**1단계에서 제외** (계속 이 PC에서만 실행):

- `mlip_*` — 사용자의 Python 환경이 필요하다.
- ~~`crest_*`~~ → **구현됨 (2026-11-23, 사용자 결정: AWS 기기에 CREST 설치 후)**:
  Linux 기기의 점검이 `crest`(`command -v`, `~/.local/bin/crest`,
  `~/.local/opt/crest/crest/crest`)를 찾고, 찾으면 그 바이너리에 force constant /
  reference 줄을 받는지 직접 묻는다(crest/env.py의 검사 스크립트를 SSH로, 바이너리별
  캐시). 배치는 CREST가 있는 기기에만, 비용은 `-T` 스레드(MPI 불필요).
  `RemoteCrestRunner`가 `.xyz`·`constraints.inp`·`coord.ref`만 올리고(이전 앙상블은
  보내지 않음) 원격 폴더에서 `crest`를 setsid로 띄운다. 그 뒤(10초 동기화, 연결 끊김,
  스팟, 폴더 전체 가져오기·검증·삭제, 취소, 재접속)는 ORCA와 같고, 가져온 폴더는 로컬
  CREST 경로(parse_crest_result, conformer 내보내기, 뒤 단계 펼치기)를 그대로 탄다.
  선호 계산에 `crest_conf`를 고를 수 있다. Windows 기기에는 CREST가 없다(Windows판
  없음). 실제 AWS 기기에서 제약(force constant 0.5) 걸린 에탄올 검색으로 확인
  (`tests/smoke/test_remote_crest_smoke.py`).
- GPU를 쓰는 계산(`uses_gpu`).
- 로컬 절대 경로를 적어 넣은 raw `.inp` (예: `%moinp "D:/…/x.gbw"`). 원격에는 그
  경로가 없다. 판별: raw 텍스트에 드라이브 경로(`[A-Za-z]:[\\/]`)가 있으면 로컬 전용.
- SLURM 같은 HPC 스케줄러 — transport 추상화만 열어 둔다.
- ~~Windows 원격 기기~~ → **구현됨 (2026-11-23, 사용자 노트북용)**: 점검이 `sh -s`
  실패(cmd.exe)나 MSYS bash 응답을 보면 PowerShell로 다시 점검하고, 실행은 WMI
  `Win32_Process Create`(SSH 세션이 끝나도 살아남음), 종료는 `taskkill /T /F`,
  나머지(10초 동기화, 폴더 전체 가져오기·검증·삭제, 선호 계산, 스팟, 정리 목록)는
  Linux와 같다. 어느 플랫폼인지는 원격 폴더 경로 모양(`C:/…`)으로 판단한다.
  이 PC의 로컬 PowerShell로 검증(실제 ORCA 1코어·MS-MPI 2프로세스 포함), 실제
  Windows sshd 너머는 미검증. 노트북 설정법은 `docs/WINDOWS_REMOTE_SETUP_KR.md`.
- 클라우드 VM 켜기/끄기 — 사용자가 직접 한다.

## 3. 사용자 쪽 준비 (D1)

기기 하나당 `~/.ssh/config`에 다음 정도를 적는다.

```
Host gcp-orca
    HostName 34.64.xx.xx
    User taewoo
    IdentityFile ~/.ssh/gcp_key
```

터미널에서 `ssh gcp-orca`가 **비밀번호 묻지 않고** 바로 접속되면 준비 끝.
원격 기기에는 ORCA(병렬 계산이면 OpenMPI도)를 사용자가 설치해 둔다.

## 4. 기기 인식

**인식되는 것**: `~/.ssh/config`의 `Host` 항목 이름. 와일드카드(`*`, `?`, `!`)가 들어간
항목은 건너뛴다. `Include`도 따라간다. 별칭은 `^[A-Za-z0-9._-]+$`이고 `-`로 시작하지
않는 것만 받는다(ssh 옵션으로 해석되는 것을 막음).

**점검(probe)**: 백그라운드 스레드에서 기기마다 한 번 접속해 작은 셸 스크립트를 돌린다.

```
ssh -o BatchMode=yes -o ConnectTimeout=8 <alias> sh -s   # 스크립트는 stdin으로
```

읽어 오는 것:

| 항목 | 방법 | 쓰임 |
|---|---|---|
| OS | `uname -s` | Linux가 아니면 사용 불가 |
| 코어 | `nproc` (물리 코어를 알 수 있으면 그것) | 자원 한도 기본값 |
| 메모리 | `/proc/meminfo`의 MemTotal / MemAvailable | 자원 한도 기본값, 여유 메모리 확인 |
| ORCA 경로 | `command -v orca`, 없으면 `~/orca*/orca`, `/opt/orca*/orca` | 실행 파일 |
| ORCA 버전 | 설치본에서 읽음 — 정확한 방법은 실제 설치본으로 확인 (P3) | 본체와 다르면 경고 |
| OpenMPI | `command -v mpirun` | 없으면 nprocs 1 계산만 받음 |
| `setsid` | `command -v setsid` | 없으면 사용 불가 |
| 작업 폴더 여유 공간 | `df -Pk <workdir>` | 표시만 |

- `BatchMode=yes` 때문에 비밀번호나 처음 보는 호스트 키 질문이 나오면 **묻지 않고
  실패**한다. 그 경우 설정 화면에 쉬운 말로 이유를 보여 준다(예: "처음 접속 확인이
  필요합니다 — 터미널에서 `ssh gcp-orca`를 한 번 실행하세요").
- `ssh` 실행 파일: Windows는 기본 포함된 `C:\Windows\System32\OpenSSH\ssh.exe`,
  Linux는 PATH의 `ssh`. 플랫폼 분기는 `sys.platform`으로만 한다(P58).
- 인식은 감지이고, 설정은 "그 안에서의 선택"만 둔다(P58, CREST의 `crest_distro`와 같은 위치).

**설정에 저장되는 것** (`config.py` `Settings`):

```python
remote_devices: list[dict]   # [{alias, enabled, max_jobs, cores, ram_mb,
                             #   orca_path, workdir}]
```

`cores` / `ram_mb` / `max_jobs`는 0이면 자동(점검값 기준), `orca_path`는 비면 점검값,
`workdir` 기본값은 `~/orcadesk-runs`.

## 5. 자동 분배 (엔진)

현재 엔진은 `ResourceBudget` 하나(이 PC)와 `_slots`(실행 중인 작업)로 admission을 한다
(`core/queue.py` `_pick_ready` → `_fits` → `_start_job`, P57). 이를 **기기별 풀**로 넓힌다.

```python
@dataclass
class DevicePool:
    id: str                 # "" = 이 PC, 그 외 ssh 별칭
    budget: ResourceBudget  # 이 기기 기준으로 resolved()
    online: bool            # 원격만 의미 있음
    caps: DeviceCaps        # mpi 유무 등 점검 결과
```

- `_JobSlot`에 `device: str` 필드를 추가한다. 기존 합계(`sum(s.cores …)`)는 같은 기기의
  slot만 더한다.
- `_fits(slot)` → `_place(calc) -> Optional[str]`: 풀을 **이 PC 먼저, 그다음 설정 목록
  순서**로 보며 처음 들어가는 기기를 고른다. 원격 풀은 다음을 모두 만족해야 한다.
  - `online`이다.
  - 계산이 원격 가능 종류다(§2).
  - nprocs > 1이면 그 기기에 OpenMPI가 있다.
  - 그 기기의 jobs / cores / RAM 한도 안에 들어간다.
  - (원격) 마지막 점검의 MemAvailable 기준 여유 메모리 확인. 지금의 `ram_headroom_mb`와
    같은 규칙이고, 그 기기에서 아무것도 안 돌 때는 건너뛴다.
- **어느 기기에도 안 들어가는 큰 계산**: 지금 규칙("예산보다 크면 이 PC에서 혼자 돈다")을
  그대로 이 PC에만 적용한다.
- **의존 관계**: `_deps_ready` / `_settled`는 바뀌지 않는다. 원격 계산은 폴더를 다
  가져온 뒤에 DONE이 되므로, 자식은 항상 본체에 있는 부모 결과로 시작한다. 그래서
  **기기를 넘나드는 의존도 그대로 된다**(이 PC에서 opt → 클라우드에서 freq).
- **살아 있는 대기열(P29)**, 승인 게이트, 에너지 조건, `_block_unreachable`은 바뀌지 않는다.
- **온라인 상태 갱신**: 실행 중에는 60초마다 원격 기기를 다시 점검한다. 오프라인
  기기는 슬롯을 내놓지 않을 뿐, 오류가 아니다.
- **업로드나 실행 시작 실패**는 계산 실패가 아니다. 그 기기를 오프라인으로 표시하고,
  계산은 다시 대기 상태(unseen PENDING)로 돌려 다음 스캔에서 다른 기기로 간다.

`resource_usage()`는 기기별 목록을 돌려준다(§9 스키마).

## 6. 원격 실행 (`RemoteOrcaRunner`)

`OrcaRunner`와 **같은 동작 이름**(launch / adopt / monitor / cancel / detach /
end_position)을 가진다. 그래서 `_run_calc`는 거의 바뀌지 않는다. 엔진의
`_orca_runner_factory`가 slot의 `device`를 보고 둘 중 하나를 만든다. 구조는
`crest/runner.py`(setsid, `/proc/<pid>/stat` 22번째 필드, 프로세스 그룹 kill,
scratch 복사)를 그대로 따른다.

### 6.1 실행 전 (본체에서, 지금과 동일)

`.inp` 쓰기, `_set_aside_stale_guess`, `_prepare_neb_side_files`,
`_stage_irc_hessian`, `_stage_template_hessian`은 지금처럼 **본체의 실행 폴더**에
작업한다. 이 함수들은 이미 모든 부속 파일을 실행 폴더 안에 **맨 파일 이름**으로
복사해 두므로 원격용 수정이 필요 없다.

### 6.2 launch

1. **원격 폴더**: `{workdir}/{run_dir}.{실행 시각}` (`run_dir`은 이미 기록되는 상대
   경로를 `/`로 바꾼 것, 실행 시각은 `YYYYMMDD-HHMMSS` 형식). **실행할 때마다 다른
   폴더**이므로, 끊긴 기기에 옛 실행이 아직 돌고 있어도 다시 돌린 계산과 절대 겹치지
   않는다(§6.3). 실행 시작 때 계산의 `remote_dir`에 절대 경로로 기록한다. 설정의
   workdir이 나중에 바뀌어도 다시 붙을 수 있게 하기 위해서다(`output_path`와 같은 이유).
2. **업로드**: 본체 실행 폴더의 최상위 파일을 **그대로** 올린다. 단, 옆으로 치워 둔
   stale 파일과 이전 `.out`은 뺀다. 로컬에서 돌 때 ORCA가 보는 폴더와 같게 하려는
   것이다(AutoStart의 `.gbw` 이어받기 등 동작이 동일해야 함). 처음 도는 계산이면
   `.inp`와 부속 파일뿐이라 작다.
3. **실행**: `setsid`로 띄우는 짧은 스크립트가 다음 일을 한다.
   - 실행 폴더로 `cd`
   - `orca {name}.inp > {name}.out 2>&1` (ORCA는 원격에서도 **절대 경로로**
     실행하고, 입력은 맨 파일 이름으로 넘긴다 — `runner.py`의 공백 문제와 같은 이유)
   - 자기 pid와 `/proc/$$/stat` 22번째 필드를 파일로 남김
4. pid와 시작 시각을 기존 `calc.pid` / `calc.create_time`에 저장한다. `calc.device`가
   비어 있지 않으면 이 값은 **원격** 프로세스 정보로 읽는다.

### 6.3 monitor

- **10초 동기화** (사용자 결정, 2026-11-23: 실시간 스트리밍은 네트워크만 잡아먹으니
  쓰지 않는다): 10초마다 **ssh 호출 한 번**으로 두 가지를 함께 받는다.
  - 생존 확인: `/proc/<pid>/stat` 22번째 필드 비교(alive/dead)
  - `.out`에 새로 늘어난 부분: 본체 `.out` 크기를 offset으로
    `tail -c +{offset+1} | head -c 4MB | base64`. 늘어난 바이트만 오고, 한 번에
    4 MB를 넘으면 다음 동기화가 이어 받는다.

  받은 바이트는 **본체의 `{name}.out`에 이어 쓰고**, 지금의 `FileTailer`가 그 파일을
  읽어 `on_line`으로 넘긴다. 진행 그래프, Jobs 화면, 재접속 시 그래프 재구성이 모두
  "로컬 파일이 자라는 중"으로 보고 그대로 동작하며, 10초씩 늦게 따라갈 뿐이다.
  동기화 사이에는 연결을 열어 두지 않는다.
  - 결과가 **알 수 없음**(접속 실패, 시간 초과)이면 계속 돈다고 보고 다음 동기화를
    기다린다. CREST `_liveness`와 같은 원칙이다(P6, 일시적 실패가 몇 시간짜리 계산을
    실패로 만들면 안 됨).
- **연결 끊김 표시 (D7)**: 다음 중 하나가 되면 계산의 `disconnected` 플래그를 켠다.
  - 동기화가 **연속 2번**(약 20초) "알 수 없음"
  한 번의 일시적 실패로는 켜지지 않으므로 표시가 깜빡이지 않는다. 동기화가 한 번
  성공하면 바로 끈다. 플래그는 `faulted`처럼 **저장하지 않는다**
  (재접속 때 다시 판단). 내부 상태는 계속 **RUNNING**이고, slot도 그대로 잡고 있다.
  같은 기기의 계산은 함께 끊기므로, 기기 단위 상태(`DevicePool.online`)와 같은 판단을
  공유한다. 어디에 어떻게 보이는지는 §8.1.
- **연결 끊김 중 결과 가져오기**: §6.4의 가져오기가 실패하는 동안에도 같은 규칙으로
  플래그가 켜진다.
- **멈춤 감시(watchdog)**: fatal 표지가 보인 뒤 폴더 최종 수정 시각을 원격에서 읽는다
  (`find . -maxdepth 1 -printf '%T@\n'`의 최댓값). `_HUNG_AFTER` 규칙은 그대로다.
- **cancel (기기에 닿을 때)**: 세션 전체(`ps -o pid= -s <pid>`의 모든 프로세스)와
  프로세스 그룹(`kill -TERM -<pgid>`)에 TERM, 시간 안에 안 죽으면 `-KILL`.
  mpirun은 rank마다 프로세스 그룹을 따로 만들어 그룹 kill만으로는 rank가 살아남는다.
  dash(우분투 `/bin/sh`)는 `kill -TERM -- -<pgid>`를 거부하므로 `--`를 쓰지 않는다.
  그다음 §6.4대로 폴더를 가져오고(ORCA 임시 파일 `*.tmp`, `*.tmp.<n>`은 제외)
  검증 후 원격을 지운다.
- **cancel (기기에 닿지 않을 때)**: "닿지 않음"은 VM이 꺼져 ORCA가 이미 죽은 경우일
  수도, 내 쪽 네트워크만 끊겨 ORCA가 **계속 돌고 있는** 경우일 수도 있다. 둘을 구별할
  수 없으므로 다음과 같이 처리한다.
  1. 본체에서는 **즉시 CANCELLED**로 바꾸고 그 기기의 slot을 비운다. 사용자는 기다리지
     않는다. `disconnected` 플래그는 끈다(더 이상 실행 중인 계산이 아님).
  2. **정리 목록**(§7)에 `(device, pid, create_time, remote_dir, calc 이름, 실행 시각)`을
     적는다. 세션에 저장되므로 앱을 껐다 켜도 남는다.
  3. 정리가 끝날 때까지 그 계산의 `message`에 한 줄을 보여 준다:
     "취소됨 — 계산 기기에 연결되지 않아, 원격 작업은 연결되면 정리합니다"
     (②의 경우 취소 후에도 클라우드 요금이 나갈 수 있어서 알린다.)
  4. 그 기기에 **다시 닿으면**(실행 중 60초 점검, 또는 다음 실행 시작 시) 백그라운드에서
     조용히 정리한다.
     - 원격 ORCA가 아직 살아 있으면 끈다. **pid와 `/proc` 시작 시각이 둘 다 맞을 때만**
       끈다. 그사이 재사용된 pid의 엉뚱한 프로세스는 건드리지 않는다.
     - 그 계산을 **그 뒤로 다시 돌리지 않았다면**(계산의 `remote_dir`이 정리 항목과 같고
       상태가 아직 CANCELLED), D4·D5대로 폴더 전체를 본체로 가져오고 검증한 뒤 원격을
       지운다.
     - **이미 다시 돌렸다면** 옛 중간 결과는 가져오지 않는다. 새 결과를 덮어쓰면 안
       되기 때문이다. 원격 폴더만 지운다(§6.4의 경로 가드 그대로).
     - 정리가 끝나면 항목을 지우고, 3의 메시지도 원래 취소 문구로 돌린다.
  5. 사용자가 그 기기를 `~/.ssh/config`나 설정에서 빼면 그 기기의 정리 항목도 버린다.
     기기가 영영 돌아오지 않는 경우의 끝이다.
- **detach**(앱 종료): 동기화만 멈춘다. 원격 ORCA는 계속 돈다.

### 6.4 결과 가져오기 (D4, D5)

프로세스가 끝나면(정상, 실패, 취소, 멈춤 모두) **판정 전에** 다음을 순서대로 한다.

1. **목록 받기**: 원격에서 `find . -type f -printf '%s\t%P\n'`
2. **내려받기**: `sftp -b`로 폴더 전체를 본체 실행 폴더에 내려받는다(`get -R -a`,
   `-a`는 끊긴 파일을 이어 받기). 같은 이름은 덮어쓰고, 본체에만 있는 파일(치워 둔
   stale 파일 등)은 그대로 둔다. 동기화로 이어 쓴 `.out`도 원격의 정확한 사본으로
   덮인다.
3. **검증**: 1의 목록과 본체 파일을 이름·크기로 하나하나 비교한다.
4. **원격 삭제**: 검증이 통과했을 때만 `rm -rf -- <remote_dir>`.
   - 안전장치: 지울 경로는 기록된 `remote_dir`이어야 하고, workdir **아래**의
     비어 있지 않은 하위 경로여야 한다. workdir 자체, `/`, `~` 등은 거부한다.
     `realpath`로 다시 확인한다.
5. 그다음 지금의 `_monitor_and_finish` 판정 경로(parse → validate → DONE/FAILED)가
   **본체 파일**로 돈다.

- 받는 동안 계산은 **RUNNING**이다(D6). 수 GB 파일이면 몇 분 걸릴 수 있다.
- 내려받기가 실패하면 백오프로 재시도한다. 계산은 RUNNING을 유지하고 원격 폴더는
  지우지 않는다. 10분 넘게 실패하면 RUNNING 계산의 `message`에 한 줄만 쓴다:
  "결과를 가져오지 못하고 있습니다 — 계산 기기가 켜져 있는지 확인하세요."
  (사용자가 손쓸 수 있는 유일한 경우라 보여 준다. P6.)
- 앱을 껐다 켜면 §7의 재접속 경로가 가져오기부터 이어서 한다.

## 7. 저장과 재시작

`Calculation` 새 필드 (세션에 저장하고, **클라이언트 값은 받지 않음** —
`calc_from_dict` 신뢰 경계, `run_dir`과 같은 취급):

```python
device: str = ""       # "" = 이 PC. 실행 시작 때 엔진이 기록.
remote_dir: str = ""   # 원격 실행 폴더 절대 경로({run_dir}.{실행 시각}). 실행 시작 때 기록.
```

`disconnected`(§6.3)는 `Calculation`의 저장되지 않는 필드다. 세션에 쓰지 않고, 재접속 때
다시 판단한다.

**정리 목록** (`QueueStore`가 세션에 함께 저장, §6.3의 끊긴 기기 취소):

```python
remote_cleanup: list[dict]   # [{device, pid, create_time, remote_dir,
                             #   calc_name, launched_at}]
```

- 엔진이 돌지 않을 때도 정리가 되도록, 기기 점검(§4)이 성공할 때마다 그 기기의 항목을
  백그라운드에서 처리한다. UI 스레드에서는 처리하지 않는다.
- 클라이언트 값은 받지 않는다(신뢰 경계).

- **`reconcile_calcs`** (시작 시 UI 스레드): `device`가 있는 RUNNING 계산은 **손대지
  않고** RUNNING으로 둔다. UI 스레드에서 ssh를 부르면 창이 뜨기 전에 수 초씩 멈추기
  때문이다. 판정은 엔진이 다시 돌 때 백그라운드에서 한다.
- **재접속** (`_run_calc`의 RUNNING 분기): 원격 생존 확인을 한다.
  - 살아 있음 → `adopt` 후 monitor
  - 죽음 → §6.4 가져오기 → `_judge_dead_running`
  - 알 수 없음(기기 꺼짐) → RUNNING을 유지하고 그 계산의 slot을 잡은 채 주기적으로
    다시 시도. §6.3의 규칙대로 `disconnected`가 켜진다(주황색 "연결 끊김").
- 원격 RUNNING 계산이 남아 있으면 시작 시 자동 재개 흐름이 엔진을 띄워야 한다(지금의
  ORCA 재접속 자동 재개와 같은 경로).
- **keep-existing(건너뛰기)**: 본체 폴더만 본다. 원격은 보지 않는다.
- **제거 경로**: 지금처럼 파일 시스템을 건드리지 않는다. 원격도 마찬가지다.

## 8. 보이는 것 / 안 보이는 것 (D6, D7)

| 안 보임 | 보임 |
|---|---|
| 업로드·실행·동기화·가져오기·삭제 각 단계 | 설정 화면의 기기 목록과 상태 |
| 계산 카드의 "어느 기기에서 도는지" | 대기열 레일의 코어/메모리 표시: 기기별 한 줄씩 |
| 한 번의 일시적 연결 실패(30초 미만) | **연결 끊김**: 주황색 상자(§8.1) |
| 끊긴 기기 취소 후의 정리 과정 | 끊긴 기기 취소 시 한 줄 메시지(§6.3), 정리가 끝나면 사라짐 |
| | ORCA 자체 실패(FAILED, 원인은 가져온 `.out`에서) |
| | 가져오기가 오래 실패할 때 한 줄 메시지(§6.4) |

### 8.1 "연결 끊김" 표시 (D7)

`disconnected`가 켜진 RUNNING 계산은 다음 네 곳에서 주황색으로 바뀐다. 문구는 모두
"연결 끊김"이다.

| 위치 | 바뀌는 것 |
|---|---|
| 대기열 창 카드 (`qed.js`) | `data-edge="disconnected"`: 테두리 `--lost`. running의 호박색 glow는 뺀다(도는지 알 수 없으므로). stack의 뒤 카드도 같은 규칙(`data-edge`가 그 계산의 상태를 따름). |
| 대기열 레일 행 (`rail.js`) | 상태 점과 상태 문구가 `--lost` / "연결 끊김" |
| Jobs 셀 상태 칩 (`stream.js`) | `.badge.lost`: 배경 `--lost-tint`, 글자 `--lost` |
| 레일 자원 표시의 기기 줄 (`rail.js` `occupancy`) | 그 기기 줄의 이름 옆에 "연결 끊김"(`--lost`). 막대는 잡고 있는 slot 그대로 |

**색 토큰**: 지금 `DESIGN.md` §8과 `web/next/app.css`에는 주황 토큰이 **없다**
(`--ok` 초록, `--warn` 호박, `--err` 빨강, `--info` 회색뿐). 새 토큰을 제안한다.

| 토큰 | 다크 | 라이트 | 쓰임 |
|---|---|---|---|
| `--lost` | `#fb923c` | `#ea580c` | 연결 끊김 |
| `--lost-tint` | `rgba(251,146,60,.15)` | `rgba(234,88,12,.15)` | 칩 배경(15%, 기존 tint 규칙) |

- 다크: `--warn` `#fbbf24`(호박), `--err` `#f87171`(빨강) 사이의 주황.
- 라이트: `--warn` `#b45309`(어두운 호박)보다 밝고 채도가 높으며, `--err` `#dc2626`보다
  노란 쪽이다. 라이트에서 `--err`와 충분히 구별되는지는 실제 화면에서 확인한다.
- 구현 때 함께 고칠 것: `DESIGN.md` §8 토큰 표와 상태 색 표, `web/next/app.css` 맨 아래의
  이름 붙은 블록(의도적 추가), `tools/uidiff.py`의 `PER_LANDMARK_SKIP`(이유 포함). 그다음
  `uispec` / `uidiff`를 다시 돌린다.

- 공유 로그(`get_log`)에는 transport 줄을 쓰지 않는다. 대신 버그를 추적할 수 있게
  `user_data_root()/remote.log`에 회전 로그를 남긴다. 화면에는 나오지 않는다.

## 9. 코드 배치

```
orcamgr/remote/            Qt 없음 (테스트 가능)
  ssh.py       transport: run(alias, script) /
               put(alias, files, dir) / get(alias, dir, dest) / list(alias, dir)
               — ssh.exe·sftp 호출, 모든 원격 인자는 shlex.quote, 실패는 값으로 반환
  devices.py   ~/.ssh/config 파싱, 점검 스크립트, DeviceInfo
  runner.py    RemoteOrcaRunner (§6), fetch_and_verify(), safe_remove()
core/queue.py      DevicePool, _place(), _JobSlot.device, runner factory 분기,
                   재접속 분기, resource_usage() 기기별
state/store.py     Calculation.device / remote_dir 저장, disconnected(비저장),
                   remote_cleanup 정리 목록 저장, reconcile_calcs 예외,
                   make_engine_factory에 기기 풀 전달
config.py          Settings.remote_devices
gui/bridge.py      detect_devices() (백그라운드 점검 시작) + get_devices() (폴링)
state/schemas.py ↔ web/types.js ↔ web/globals.d.ts
                   DeviceInfo, RunResources.devices: list[DeviceUsage]
                   (DeviceUsage에 online: bool),
                   계산 payload에 disconnected: bool
web/next/settings.js   "계산 기기" 섹션: 목록, 상태 한 줄
                       (예: "Linux · 32코어 · 128 GB · ORCA 6.1.1"),
                       [사용] 토글, 동시 작업 수, (선택) 코어/메모리 한도, [다시 찾기]
web/next/rail.js       occupancy(): 기기별 meter 줄, 끊긴 기기 줄 표시;
                       행의 상태 점·문구 "연결 끊김" (§8.1)
web/next/qed.js        카드 data-edge="disconnected" (§8.1)
web/next/stream.js     Jobs 셀 상태 칩 .badge.lost (§8.1)
web/next/app.css       --lost / --lost-tint 토큰과 위 규칙 (맨 아래 이름 붙은 블록)
```

## 10. 보안

- 키 인증만 쓴다. ORCAdesk는 비밀번호나 키를 저장하지 않는다(사용자의 ssh 설정에
  위임).
- `BatchMode=yes`라 대화형 질문이 없고, 호스트 키는 사용자의 `known_hosts`를 따른다
  (기본 `StrictHostKeyChecking`).
- 별칭은 파싱된 config 이름 중 §4 정규식을 통과한 것만 쓴다. 원격 명령의 모든 경로와
  이름은 `shlex.quote`로 감싼다. 계산 이름 제한(공백, `&` 등 거부)은 이미 있다.
- 원격 삭제는 §6.4의 경로 가드를 통과할 때만 한다.

## 11. 테스트

- `FakeTransport`(CREST fake runner 방식): `RemoteOrcaRunner`의 launch, monitor,
  일시적 끊김, cancel, detach, 가져오기 검증 실패 시 삭제 안 함, 삭제 가드 거부.
- `disconnected` 플래그: "알 수 없음" 1번으로는 안 켜짐, 연속 2번이면 켜짐, 성공 1번이면
  꺼짐, 켜져도 상태는 RUNNING이고 slot 유지, 세션에
  저장되지 않음.
- 정리 목록: 끊긴 기기 취소 → 즉시 CANCELLED·slot 반환·항목 저장·메시지; 기기 복귀 시
  pid+시작 시각이 맞을 때만 kill(안 맞으면 kill 안 함); 다시 돌리지 않았으면 가져오기→
  검증→삭제, 다시 돌렸으면 가져오지 않고 삭제만; 기기 제거 시 항목 폐기; 세션 왕복;
  실행마다 `remote_dir`이 달라 다시 돌린 계산과 겹치지 않음.
- 엔진: 가짜 기기 풀로 배치 순서, 원격 불가 종류는 로컬, OpenMPI 없는 기기, 오프라인
  기기 무시, 업로드 실패 시 다른 기기로, 기기 간 의존.
- store: `device` / `remote_dir` 저장 왕복, `calc_from_dict`가 무시하는지,
  `reconcile_calcs`가 원격 RUNNING을 건드리지 않는지.
- `devices.py`: ssh config 파싱(와일드카드, Include, 이상한 별칭 거부).
- `test_bridge_slots.py`가 새 슬롯을 잡는지 확인.
- 실제 기기: `ORCADESK_SMOKE_REMOTE=<alias> python -m pytest tests/smoke/test_remote_ssh_smoke.py`
  — 가짜 ORCA(실제 출력을 재생, 표시 없는 입력은 거부)로 SSH 경로 전체를 확인.
  2026-11-23 WSL(Ubuntu 24.04, 포트 2222)에서 통과: 점검, 업로드, 실행, 10초 동기화,
  가져오기·검증·삭제, 취소, sshd 30초 중단 시 연결 끊김 표시와 복구.
  opt-in 섹션 — 짧은 opt+freq 체인을 이 PC → 원격 → 이 PC로 넘겨 돌린다.

## 12. 구현 순서

1. `remote/ssh.py` + `devices.py` + 설정 "계산 기기" 섹션 — 점검만 돼도 연결 확인 용도로 쓸 수 있다.
2. `RemoteOrcaRunner` + 가져오기/검증/삭제 (FakeTransport 테스트).
3. 엔진 기기 풀, 배치, 저장, 재접속.
4. 레일 기기별 meter, 문서(ARCHITECTURE / CLAUDE.md / PRINCIPLES P57 / DESIGN / README), smoke.

## 13. 구현 때 같이 고칠 문서

- `CLAUDE.md`: 아키텍처 표에 `remote/` 행 추가. Queue invariants에 "원격 계산은 폴더를
  다 가져온 뒤 DONE" 추가.
- `PRINCIPLES.md` P57: admission이 기기별 풀이라는 점.
- `DESIGN.md`: 설정 섹션, 레일 meter, §8 토큰 표에 `--lost` / `--lost-tint` 추가와 상태
  색 표에 "연결 끊김 = 주황"(§8.1), §15 체크리스트. `app.css` 맨 아래 이름 붙은 블록과
  `tools/uidiff.py` `PER_LANDMARK_SKIP`도 함께.
- `docs/ARCHITECTURE.md`: 새 절 *Remote devices*(이 문서의 확정본).
- `README.md` / `README.ko.md`: 원격 기기 사용법(§3).

## 13a. 스팟 기기 (2026-11-23 사용자 결정)

클라우드 VM을 스팟(선점형)으로 쓰므로 기기마다 두 가지를 고를 수 있다.

- **선호 계산 (`prefer`)**: 기기마다 ORCA 계산 종류를 체크한다(체크 없음 = 선호 없음).
  **선호일 뿐 제한이 아니다**(사용자 결정 — 처음의 "단일점만" 고정 필터를 대체):
  자리가 난 기기는 선호하는 종류의 준비된 계산을 먼저 받고, 그런 계산이 없으면
  다른 ORCA 계산도 받는다. 엔진은 두 번 훑는다 — 첫 번째는 이 PC, 그 종류를 선호하는
  기기, 선호가 없는 기기에만 놓고, 그래서 아무것도 못 놓았을 때만 두 번째에서 선호
  밖의 기기도 허용한다. 스팟 VM이면 보통 단일점(`sp`, `tddft`, `nmr` — 구조를
  움직이지 않아 잃어도 그 계산 시간만 손해)을 체크한다. 옛 `kinds: "single_point"`
  설정은 이 세 종류의 선호로 옮겨진다. 배치 순서(이 PC 먼저)는 그대로다.
- **스팟 VM (`spot`, `requeue_after_min` 기본 5분)**: 스팟 기기의 계산이
  - **연속으로** 그 시간 넘게 응답이 없거나 — 응답이 한 번이라도 오면 다시 0부터
    센다. 연결이 들락날락해도 한도 안에 돌아오면 계산은 그대로 이어진다.
  - 정전 뒤 다시 연결됐는데 계산이 죽어 있고 `.out`에 정상 종료 줄도 오류 표지도
    없으면(=VM이 회수됨, ORCA가 실패한 게 아님) —
  
  계산을 PENDING("Re-queued: the spot device stopped answering.")으로 돌려 다시
  돌린다(이 PC든 다른 기기든). FAILED로 잠그지 않는다(P24). 옛 원격 폴더는 기기가
  돌아오면 pid·시작 시각이 맞을 때만 프로세스를 끄고 **가져오지 않고** 지운다(정리
  항목 `fetch: false`). ORCA 자체 오류(error termination)는 지금처럼 FAILED.
  스팟이 아닌 기기는 지금처럼 RUNNING + 주황 "연결 끊김"을 유지한다.
- **고정 IP**: `~/.ssh/config` 항목은 VM을 껐다 켜도 그대로 쓴다. 인스턴스를
  **새로 만들면** 호스트 키가 바뀌어 BatchMode가 거부하고, 설정 화면이 "호스트 키가
  바뀜 — 터미널에서 `ssh <별칭>`을 한 번 실행"이라고 알려 준다.

## 14. 남은 질문

- **배치 순서**: 이 PC 먼저(무료) → 원격. 반대(무거운 것부터 클라우드)가 필요하면
  기기별 "우선" 옵션을 2단계에서 검토한다.
