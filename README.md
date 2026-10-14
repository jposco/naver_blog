# naver_blog

네이버 블로그 원고 관리 저장소. 집/회사 PC에서 동일하게 작업한다.

## 구조

```
posts/YYYY/YYYY-MM-DD-slug/
  post.md        # 원고 (frontmatter의 status로 진행상태 관리)
  images/        # 해당 글 이미지 (업로드 전 1MB 이하로 압축)
templates/post.md  # 새 글 템플릿
tools/thumbnail/   # 대표이미지 생성기 (사진 GPS → 지도 핀 + 사진 카드)
CLAUDE.md          # Claude 작업 규칙
```

## 작업 방법

**A. Claude Code 웹 (claude.ai/code)** — 설치 불필요, 어느 PC든 브라우저로 이 저장소 열고 작업. 세션 끝나면 Claude가 커밋/푸시.

**B. 로컬 PC** — 작업 전후 반드시:

```bash
git pull          # 시작 전
git add -A && git commit -m "메모" && git push   # 끝난 후
```

pull 안 하고 작업하면 충돌 난다. 한 글은 한 번에 한 PC에서만 편집.

## 발행

네이버 블로그는 글쓰기 API가 없다 → `post.md` 내용을 SmartEditor에 붙여넣고 이미지 수동 업로드. 발행 후 `status: published`, `published_url` 기입.

## 사진 올리는 법 (대표이미지용)

원본 파일(위치정보 포함)이 그대로 저장소에 들어가야 한다.

1. 구글 포토 앨범 → ⋮ → **모두 다운로드** (zip) → 압축 해제
2. GitHub 저장소 → `posts/.../photos/` 폴더 → **Add file → Upload files** → 사진 끌어다 놓기 → Commit
   (또는 로컬에서 폴더에 복사 후 git push)

카톡/메신저로 받은 사진, 스크린샷, 채팅창 첨부 이미지는 위치정보가 지워져 있어 안 된다.

## 대표이미지 생성

```bash
pip install -r requirements.txt && python -m playwright install chromium   # 로컬 최초 1회
export GOOGLE_MAPS_API_KEY=...                                               # Static Maps API 키
python tools/thumbnail/thumbnail.py scan   posts/2026/2026-10-14-tokyo-tower-photo-spots/thumbnail.yaml
python tools/thumbnail/thumbnail.py render posts/2026/2026-10-14-tokyo-tower-photo-spots/thumbnail.yaml
```
