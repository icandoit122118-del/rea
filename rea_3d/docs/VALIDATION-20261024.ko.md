# 2026-10-24 실제 검증 요약

**이 문서는 눈동자 블렌딩 수정 전의 검증 기록입니다.** 아래에서 발견한 LIris/RIris 누락은 후속 수정으로 적용했고, 최신 결과는 [블렌딩 수정·재생 검증](IRIS-BLENDING-FIX.ko.md)을 참조하세요. 아래 성능 수치는 수정 후 파이프라인의 재측정값이 아닙니다.

기존 로컬 원시 증거에서 정리한 결과입니다. 게임 파일·추출 모델·화면·브라우저 프로필·개인 경로를 공개 저장소에 포함하지 않았습니다. 이 보고서와 수치가 실제 원본 파일 또는 전체 원시 증거를 대체하지는 않습니다.

| 대상 | 관측 결과 | 경계 |
| --- | --- | --- |
| 기존 GLB 8종 | 실제 브라우저 8/8종·56/56개 항목 통과; 구조 오류0·경고73 | ORAS에서 새로 추출한8종이 아님 |
| 애니메이션 | 합계47클립 inventory, 종별 첫clip RAF 진행·본 변화와 마지막clip 선택·렌더 확인 | 47클립 전수 재생 아님 |
| 셰이더 | 78draw, GLSL8/8 compile·program4/4 link, GL 오류0, UV행렬234개 업로드값 일치 | 원작 셰이더 bytecode 실행·원작UV수학 일치 증거 아님 |
| 재질 상태 | 깊이·RGBA mask·cull·stencil은 표본 기대값과 일치 | LIris/RIris alpha blending은 실제BLEND=false로 불일치2건 |
| 추가 종 원본 추출 | GARC payload 및 제공ZIP 직접읽기 실패 | export 성공0, 범용성 보류 |
| 성능 | 13조건×3회, 23,427계측프레임·동수 유효GPUquery, 약390.438초 | 현구현·현PC·headlessRAF 한정 |

표본은 리자몽·거북왕·피카츄·팬텀·롱스톤·잉어킹·갸라도스·뮤입니다. 로컬에 보관된 06wj/pokemon commit 00d96f7f18894055e7f1db44fa0df6462e5e4c8a의 provenance와 GLB 내부식별자·Gitblob·크기·클립을 교차검산했습니다. 종별 원작 게임 출처나 원격commit을 이번에 재확인하지는 않았습니다.

성능 환경은 Ryzen5 5600 / 약32GB RAM / RX9060XT / AMD driver32.0.32015.7029 / 격리Chromium ANGLE D3D11입니다. 실제buffer400×240·720p·1080p, DPR1, 회차별5초예열+10초측정입니다. 일반재생기 source/fallback6조건, fanUI3조건, 같은이상해씨6/12개 복제장면4조건입니다. 33ms이상RAF간격1개,50ms이상0개입니다.

| 조건 | 평균FPS | callbackCPU p95 | rendererCPU p95 | GPU p95 |
| --- | ---: | ---: | ---: | ---: |
| 단일source1080p | 60.00 | 1.40ms | 0.90ms | 0.0698ms |
| fanUI1080p | 60.00 | 0.90ms | 0.50ms | 0.0752ms |
| 6개source720p | 60.01 | 2.40ms | 1.30ms | 0.0659ms |
| 12개source720p | 60.05 | 5.00ms | 2.70ms | 0.0857ms |
| 12개fallback720p | 60.00 | 5.70ms | 3.70ms | 0.2260ms |

CPU범위는 서로 포함되므로 합산하지 않습니다. 약60Hz 기본headless스케줄이므로 최대FPS나 실제화면출력률·스마트폰 성능을 입증하지 않습니다. source/fallback의 실제draw/triangles가 다르고 source에 위blend누락이 있으므로 동등한 원작파이프라인에서 셰이더 속도우위를 입증한 결과도 아닙니다. ROM추출/변환속도·실제VRAM바이트는 측정하지 못했습니다.

fanUI의 새profile첫모델draw는 약2.28~2.89초, 같은context반복load는0.224~0.302초였습니다. HTTPcache는비활성,OS/driver/shadercache는분리하지 않았고 계측오버헤드가 포함됩니다. 초기표시지연과 눈동자blend2건이 후속개선대상입니다. 원작외부shader·조명·외곽선·다중pass·동일framepixelparity는 미완료입니다.

[13조건 CSV](performance-summary.csv) · [CPU/GPU p95 그래프](frame-costs.svg)
