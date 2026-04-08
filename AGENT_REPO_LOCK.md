# CRITICAL REPO SAFETY CONTRACT

Authorized repo root:
D:\dev\tcg-watcher-worktree

Before every task:
1. print cwd
2. print git top-level root
3. print active branch

If repo root is NOT exactly:
D:\dev\tcg-watcher-worktree

STOP immediately and report:
WRONG REPO

Forbidden:
- D:\docker\tcg-watcher
- any parent directory
- any unrelated worktree
- delete or cleanup commands outside explicit approval