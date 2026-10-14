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
