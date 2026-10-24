# 눈동자 블렌딩 수정과 실제 재생 검증 — 2026-10-24

[기계 판독용 검증 요약과 증거 해시](iris-blending-verification.json)를 함께 제공합니다.

LIris/RIris에 원본 PICA 데이터가 요구하는 alpha blending을 실제 재생기에 적용했습니다. 기존에는 값을 읽어 보존했지만 RawShaderMaterial 생성 시 모든 재질에 NoBlending을 강제했습니다.

## 수정 내용

`preview/pica-player.js`의 `blendMaterialParameters`가 재질 이름에 의존하지 않고 decoded blend 설정을 Three.js에 연결합니다. RGB와 alpha의 계수·연산을 따로 설정하며, 두 Iris는 다음 값을 사용합니다.

| 상태 | 값 |
| --- | --- |
| blending | CustomBlending / 실제 GL BLEND=true |
| RGB source / destination | SRC_ALPHA / ONE_MINUS_SRC_ALPHA |
| alpha source / destination | SRC_ALPHA / ONE_MINUS_SRC_ALPHA |
| RGB / alpha equation | FUNC_ADD / FUNC_ADD |
| transparent | true |
| depth test / depth write | true / false, 기존 원본 설정 유지 |

Three.js NormalBlending은 alpha source를 ONE으로 설정하므로 사용하지 않았습니다. 원본이 FuncAdd·One/Zero인 몸통·눈 바탕 재질은 수식상 동치인 기존 불투명 경로를 유지합니다. source blending이 비활성이면 비활성으로 유지하고, 알려지지 않은 enabled 계수·연산을 조용히 무시하지 않습니다. 상수 혼합을 사용하는 설정은 decoded RGBA 색을 요구하고 재질 애니메이션의 blend 색을 갱신합니다. 이번 실제 GPU 표본은 위 두 Iris의 설정이며 모든 혼합 변형을 전수 검증한 것은 아닙니다.

## 실제 GPU 상태

입력은 이전에 원본 기반으로 추출한 같은 이상해씨 GLB입니다. GLB를 다시 변환하거나 수정하지 않았으며 실행 전후 SHA256이 같습니다.

- GLB SHA256: `e8bd5ccb7524170842037ef5d67286d1f3e2c197896bad2812bde673c63a3b76`.
- 수정 전 player SHA256: `dab8ca1d8aac62022caceeeba1c5b1b2ab59d2f8eff308093fa9b95ce8fb3184`.
- 수정 후 player SHA256: `6f2b9621362d0deecc9ae88953dd30a33941c237043c9687a0da40156a87fbc7`.
- 새 격리 Chromium/WebGL2의 actual draw 직전에 driver 상태를 읽었습니다.
- 총174 draw, 각 Iris29 draw에서 BLEND와 RGB/alpha 계수·식·깊이 쓰기 값이 기대와 일치했습니다.
- 5재질의 보존 raw PICA 명령 독립 재해석, UV행렬522개 업로드 비교, shader8/8 compile·program4/4 link, GL 오류0을 확인했습니다.
- 검사한 의미 있는 상태 불일치가2건에서0건으로 바뀌었습니다. 몸통3재질의 One/Zero 비활성 대체는 동치로 분류합니다.

## 재생과 화면 비교

동일 GLB·카메라·source frame·본 회전의 수정 전/후11조건을 비교했습니다. 정면 source frame12에서는 눈 주변1,268픽셀이 바뀌었고, 감은 눈2조건과 후면에서는 차이가0이었습니다. 이 값은 이번 수정에 따른 화면 차이이며 원작과의 일치율이 아닙니다.

실제 RAF 재생에서는 clip5의9샘플과 clip11의6샘플이 각각 다른 source frame으로 진행했습니다. 골격·재질·가시성의 시간, actual draw의 눈동자 blend 상태와 GL UV 업로드를 함께 확인했습니다. source→fallback→source 전환 후 상태가 복원되고 `/fan`에서도 시간 진행·원본 blend 상태·GL 오류0을 확인했습니다. 대표 PNG와 로컬 원시 영수증은 별도 검증 폴더에 보존했습니다.

## 별도 GPU 합성 계산

실제 `attachPicaPlayer`를 사용하는 합성 fixture에서 RGBA8 framebuffer를 읽어6개 조건을 확인했습니다. source `[1,.2,0,.5]`, destination `[.2,.4,.6,.8]`의 기대값은 `[.6,.3,.3,.65]`이며, 관측값은8비트 `[153,76,77,166]`으로 기대 양자화값과 최대1byte 차이였습니다. 알파 source를 ONE으로 바꾼 일반 혼합의 `.9`와 구분되는 결과입니다.

alpha0·alpha1, 불투명 물체에 의한 가림, backface, Iris의 depthWrite=false 뒤 후속pass도 통과했습니다. 첫 fixture 실행에서는 Three의 premultiplied clear색을 straight RGBA로 가정한 검사 조건이 잘못돼 실패했습니다. 생산 재생기를 바꾸지 않고 합성 context에 premultipliedAlpha=false를 명시하고 clear픽셀 `[51,102,153,204]`까지 직접 확인한 두 번째 실행에서6/6 통과했습니다. 첫 실패 기록도 로컬에 보존했습니다.

## 회귀 검사

- 블렌딩 매핑·재질·시간 동기·GPU 상태 비교기 관련21개 검사 통과.
- 공개 사본 전체 Node90항목 중88개는 첫 실행에서 통과했습니다. 선택적 공식 REA 정적 분석 연동의 SDK 요청이60초에 시간 초과하면서 해당 subtest와 부모 test가 실패했습니다. 이 두 항목은 같은 소스의 단독 재실행에서 통과했습니다(연동 subtest 약24.7초). 첫 실패 기록도 보존했으며 단일 전체 실행90/90 성공으로 표현하지 않습니다.
- Python94항목 중89개 통과·5개 건너뜀, 실패0. 원본을 선택하지 않은 검사는 건너뜀으로 유지합니다.

## 남은 범위

원작 외부 shader program, 원작 카메라·조명·외곽선·다중 pass·정밀도와 동일 픽셀 재현은 이 수정으로 완료되지 않았습니다. 이전13조건 성능 표는 수정 전 파이프라인의 값입니다. 이번에는 블렌딩 수정 후13조건 전체 성능을 재측정하지 않았습니다. 원본 다종 추출도 기존 저장장치 읽기 문제 때문에 별도 미확인으로 남아 있습니다.

게임 모델·텍스처·화면·영상·브라우저 프로필·개인 경로가 있는 raw 증거는 공개 소스에 넣지 않았습니다. 검증 스크립트와 이 요약만 배포합니다.
