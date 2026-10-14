# naver_blog

네이버 블로그 원고 관리 저장소. 집/회사 PC에서 동일하게 작업한다.

## 구조

```
posts/YYYY/YYYY-MM-DD-slug/
  post.md        # 원고 (frontmatter의 status로 진행상태 관리)
  images/        # 해당 글 이미지 (업로드 전 1MB 이하로 압축)
templates/post.md  # 새 글 템플릿
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
