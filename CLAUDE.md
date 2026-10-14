# Claude 작업 규칙

## 저장소 목적
네이버 블로그 원고 작성/관리. 집·회사 PC가 공유하므로 작업 결과는 항상 커밋·푸시한다.

## 새 글
- 경로: `posts/YYYY/YYYY-MM-DD-slug/post.md` (slug는 영문 소문자-하이픈)
- `templates/post.md` 복사 후 작성, frontmatter 채우기
- 이미지는 같은 폴더 `images/`에 `01.jpg` 순번으로

## 작성 스타일 (네이버 블로그 기준)
- 한국어, 짧은 문단(2~3문장), 소제목(##) 활용
- 핵심 키워드는 제목·도입부·소제목에 자연스럽게 포함 (과도한 반복 금지)
- 마크다운 표/코드블록은 SmartEditor 붙여넣기 시 깨질 수 있으니 최소화
- 사실 확인 안 된 정보는 쓰지 않거나 표시

## 상태 관리
`status`: idea → draft → review → published. 발행 후 `published_url` 기입.

## 대표이미지 (tools/thumbnail)
- 글 폴더의 `thumbnail.yaml` + `photos/`(EXIF GPS 포함 원본) → `out/thumbnail.png` (1360×1200)
- `python tools/thumbnail/thumbnail.py scan <yaml>` 로 GPS 확인 → `render <yaml>` 로 생성
- 지도는 Maps Static API, 장소명은 Places API (New) (`GOOGLE_MAPS_API_KEY` 환경변수). 키를 파일에 쓰거나 커밋하지 말 것
- scan 의 장소 묶음(반경 40m) 확인 → `names <yaml> --write` 로 Places API 장소명 1순위 후보를 `places`에 기입 → 후보 목록 보고 name/en 다듬고 desc·cover 채우기. 장소명은 사용자 확인 받기
- Places API는 가게·역·공원 같은 POI만 준다. 골목·교차로·경사로 같은 스팟은 후보가 엉뚱하므로 직접 이름 짓기
- 장소는 최대 6곳 (좌우 카드 3칸씩)
- 렌더 후 반드시 이미지를 직접 열어 겹침/가독성 확인. 문제 있으면 `map.zoom`/`center`, `cover`로 조정
- 구글 로고·저작권 표기는 가리지 않는다 (약관)
