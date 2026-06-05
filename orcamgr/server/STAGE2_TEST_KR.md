# 폰 연동 서버 — 2단계 테스트 (실제 ORCA 실행)

2단계는 큐를 **실제로 ORCA로 돌리는** API를 추가했습니다. 아직 폰/QR/터널은
없고, 이 PC에서 서버를 통해 계산이 돌아가는지 확인합니다.

## 전제
- 1단계가 정상 동작했음 (서버 뜨고 /api/health 응답)
- ORCA가 설치돼 있고, **데스크탑 앱 Settings에서 ORCA 경로를 한 번 저장**했음
  (서버는 그 저장된 설정을 읽습니다)

## 실행
```
python -m orcamgr.server.run
```

## 테스트 (브라우저 /docs 페이지에서)

1. **계산 추가** — `POST /api/queue`에 실제 분자로:
   ```json
   {
     "name": "h2-opt",
     "kind": "opt",
     "charge": 0,
     "multiplicity": 1,
     "geometry_source": "direct",
     "xyz": "H 0 0 0\nH 0 0 0.74",
     "config": { "kind": "opt", "functional": "wB97X-D4",
                 "basis_set": "def2-SVP", "calculation_type": "TightOpt",
                 "scf_convergence": "TightSCF", "ri_approximation": "RIJCOSX",
                 "options": "def2/J" }
   }
   ```

2. **실행 시작** — `POST /api/run` (body 없음) → `{"ok":true,"running":true}`
   - ORCA 경로가 설정 안 됐으면 400 에러 + 안내가 옵니다.

3. **로그 폴링** — `GET /api/log?since=0` 반복 호출
   → ORCA 출력이 lines 배열로 쌓입니다. 다음엔 응답의 latest 값을
     since에 넣어 새 줄만 받습니다. (예: /api/log?since=12)

4. **상태 확인** — `GET /api/queue`
   → h2-opt의 state가 pending → running → done 으로 바뀝니다.

5. **취소** (긴 계산일 때) — `POST /api/cancel`

## 확인 포인트
- /api/run 직후 응답이 **즉시** 온다 (계산이 끝날 때까지 안 기다림 = 백그라운드 실행)
- /api/log 로 ORCA 출력이 실시간으로 쌓인다
- /api/queue 의 state가 단계적으로 바뀐다
- 끝나면 running=false, 로그에 "Queue finished."
- 계산 폴더가 workspace에 생긴다 (h2-opt/h2-opt.out 등)

문제 생기면 명령창 + /api/log 출력을 알려주세요.
