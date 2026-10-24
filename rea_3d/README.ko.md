# rea_3d 설치·사용 안내

모델·텍스처·골격 모션을 검사하고 GLB/PNG로 내보내는 로컬 3DS 자산 MCP 도구입니다. 전용 Three.js 재생기는 해석한 눈·재질·가시성·보조 포즈를 함께 평가합니다. 상위 REA 본체와 별도로 실행하는 프로젝트이며 REA 제공자나 기존 앱 설정에 자동 통합되지 않습니다.

## 설치

Windows와 Node.js 22.19.0 이상이 필요합니다. GitHub에서 저장소를 받은 뒤 `rea_3d` 폴더에서 실행합니다.

```powershell
git clone --branch codex/rea-3d-initial-20261024 --single-branch https://github.com/icandoit122118-del/rea.git
cd rea/rea_3d
```

```powershell
npm ci --ignore-scripts
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup-runtime.ps1
node src/cli.mjs capabilities
npm run verify
node scripts/make-demo.mjs
```

Python 설치 스크립트는 python.org의 고정된 Python 3.12.10 embeddable ZIP을 내려받아 SHA256을 확인한 뒤 이 폴더의 `runtime/python`에만 풉니다. 글로벌 Python이나 기존 REA·TUS 설정은 변경하지 않습니다. 이미 `runtime/python`이 있으면 덮어쓰지 않습니다.

`make-demo.mjs`는 자체 생성한 삼각형·텍스처·모션을 실제 MCP SDK로 추출·변환하고 Khronos validator로 검사합니다. 생성 결과는 `examples/synthetic/latest.json`에 기록됩니다. 이 합성 샘플의 성공은 실제 게임 모델 재현 증거와 구분됩니다.

## MCP 등록

`mcp-config.json`의 `C:/path/to/rea_3d`를 실제 경로로 바꿔 사용하는 클라이언트에 등록합니다.

```json
{"mcpServers":{"rea_3d":{"command":"node","args":["C:/path/to/rea_3d/src/mcp.mjs"],"cwd":"C:/path/to/rea_3d"}}}
```

서버 시작과 현재 대화의 도구 노출은 다릅니다. 등록 후 클라이언트에서 재연결 또는 세션 새로 열기가 필요할 수 있습니다.

| 도구 | 역할 |
| --- | --- |
| rea3d_capabilities | 실제 파서·내보내기 지원 범위와 런타임 확인 |
| rea3d_inspect | 명시적으로 고른 모델·텍스처·모션 파일 검사 |
| rea3d_export | 모델과 동반 파일을 새 폴더에 GLB/PNG로 저장 |
| rea3d_archive_inspect | GARC v4/v6 테이블·범위 검사 |
| rea3d_archive_extract | 선택한 GARC의 원본 멤버 추출 |
| rea3d_analyze_code | 별도로 설치한 공식 REA CLI를 통한 JavaScript 정적 분석 |

## CLI와 재생기

```powershell
node src/cli.mjs inspect --input C:/assets/model.pc
node src/cli.mjs export --input C:/assets/model.pc --motion C:/assets/motion.pc --out C:/assets/new-export
node src/cli.mjs archive-inspect --input C:/assets/archive.garc
node src/cli.mjs archive-extract --input C:/assets/archive.garc --out C:/assets/new-members
node src/preview-server.mjs --input C:/assets/new-export/model.glb --port 0
```

입력은 절대 경로, 출력은 존재하지 않는 새 폴더를 사용합니다. 동반 모션·텍스처를 파일명이 비슷하다는 이유로 자동 연결하지 않습니다. `fps` 기본값 30은 원본 프레임 번호를 초로 해석하는 값이며 보간 프레임을 추가하는 옵션이 아닙니다. 지원하지 않는 내용은 부분 변환과 경고에 남깁니다.

출력된 localhost 주소에서 클립·속도·재생 위치와 재질 모드를 고를 수 있습니다. `/fan`은 별도 팬게임 UI 데모이며 실제 원작 게임 화면이 아닙니다. 원본 모드와 일반 GLB 모드는 구현 범위와 draw 수가 달라 순수 셰이더 성능 비교로 해석하지 않습니다.

## 현재 확인한 범위

실제 검증 요약은 [2026-10-24 보고서](docs/VALIDATION-20261024.ko.md)에 있습니다. 기존 GLB 8종 재생은 통과했지만 ORAS 원본의 다종 추출은 원본 장치 읽기 오류로 보류입니다. 눈동자 LIris/RIris의 실제 alpha blending 누락이 알려져 있으며, 원작 외부 셰이더·조명·외곽선·동일 픽셀 재현은 완료되지 않았습니다. 특정 PF 얼굴과 GF1 보간·보조 포즈의 특수 처리는 검증된 모델/입력 hash에 제한됩니다.

이 저장소는 소스·테스트·자체 합성 데이터 생성기를 배포합니다. 게임 ROM, 추출 모델·텍스처, 플레이 영상, 브라우저 프로필과 로컬 인증 정보는 포함하지 않습니다. 원본 자산은 사용자가 별도 폴더에 준비해야 합니다.

개발용 브라우저·GPU·Blender 감사 스크립트는 별도 의존성과 명시적 입력을 요구합니다. 과거 실측에 쓰인 기본 경로는 예시로 바꿨으며, 재실행 전 자신의 환경에 맞게 확인해야 합니다.

선택한 원본 PB/PK 멤버로 추가 회귀 검사를 실행할 때만 `REA3D_ORAS_MEMBERS_DIR` 환경 변수에 해당 폴더의 절대 경로를 지정합니다. 기본 테스트는 원본을 자동 탐색하지 않으며, 선택하지 않은 원본 검사는 건너뜀으로 기록합니다. 공식 REA CLI가 설치되지 않은 PC에서는 해당 선택적 코드 분석 검사만 건너뜁니다.

## 라이선스

프로젝트는 GPL-3.0-or-later입니다. 원본 reader와 LICENSE, 고정 upstream commit은 `vendor/n3ds_importer`에 보존했습니다. [출처와 적용 범위](PROVENANCE.md)를 참고하세요.
