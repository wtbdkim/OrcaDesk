# 폰 연동 서버 — 테스트 안내

## 준비 (한 번만)
프로젝트 폴더(main.py 있는 곳)에서 명령창(cmd):
```
pip install -r requirements-server.txt
```

## 서버 실행
```
python -m orcamgr.server.run
```
뜨면 성공. 단, 폰에서 접속하려면 같은 네트워크여야 함(같은 와이파이, 또는
폰 핫스팟에 PC 연결). 서버를 LAN에 열려면 run.py의 HOST를 "0.0.0.0"으로.

## PC에서 확인
- http://127.0.0.1:8000/api/health  → {"status":"ok",...}
- http://127.0.0.1:8000/            → 모바일 UI 페이지
- http://127.0.0.1:8000/docs        → API 문서

## 폰에서 접속 (4단계 핵심)
1. PC의 IP 확인 (cmd에서 `ipconfig` → IPv4 주소, 예: 192.168.0.5 또는 핫스팟 IP)
2. 폰 브라우저에서: `http://<그IP>:8000/`
   → ORCAdesk 모바일 화면이 뜸 (별도 앱 설치 불필요)
3. 이제 폰에서:
   - Queue 탭: PC의 실제 큐가 보임 (1.5초마다 자동 새로고침)
   - + 탭: 새 계산 추가 → 실제로 큐에 들어감 (PC 앱에도 반영)
   - 큐 항목의 × : 삭제
   - Run queue: 실제로 PC가 ORCA 계산 시작 (단, PC에 ORCA 경로 설정돼 있어야)
   - Log 탭: 실행 로그가 실시간으로 흘러나옴 (1초마다 폴링)
4. 상단 우측 뱃지가 "connected"면 서버 연결됨, "offline"이면 끊김.

## 주의
- Run을 누르면 PC가 진짜로 ORCA를 돌림 → PC Settings에 ORCA 경로가
  설정돼 있어야 함 (없으면 "ORCA executable is not set" 에러).
- 결과(Results) 화면은 아직 다음 단계 (요약 파싱 API 예정).
- 아직 인증 없음 — 같은 네트워크 누구나 접속 가능. 외부 노출(터널)과
  토큰 인증은 다음 단계.

## 멈추기
명령창에서 Ctrl+C.

---

## 5a 추가: QR 연결 + PIN + 연결 상태 (LAN)

PC 앱에서 서버를 켜면 이제 Settings에:
- 접속 주소 + **6자리 PIN**
- **QR 코드** (주소+PIN이 들어있음)
- "N phones connected" (연결된 폰 수)

폰에서:
- 카메라로 QR 스캔 → 브라우저 열리며 PIN 자동 입력 → 바로 연결 (입력 불필요)
- 또는 주소 직접 입력 시 PIN 입력 화면
- 연결되면 5초마다 하트비트 → PC에 "연결됨" 수로 표시

(qrcode + pillow 필요: requirements-server.txt에 포함)

다음(5b): 이 주소를 LAN IP 대신 Cloudflare 터널 URL로 바꾸면 외부에서도 접속.
