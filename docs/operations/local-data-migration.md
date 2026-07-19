# 本地单用户数据迁移

该流程把历史数据目录中的一个真实用户迁移为 `local-user`。其他比赛、demo、smoke 和测试用户只保留在完整备份中，不进入根目录 `data/`。

## 前置条件

1. 在仓库根目录执行命令。
2. 先停止前后端，避免 SQLite WAL 在迁移时继续变化：

   ```powershell
   .\scripts\stop-dev.ps1
   ```

3. 确认源用户 ID。只读盘点不会写入文件：

   ```powershell
   tutor migrate-local-data --repo-root . --target-user-id local-user --source-user-id <历史用户ID> --dry-run
   ```

## 执行迁移

```powershell
tutor migrate-local-data --repo-root . --target-user-id local-user --source-user-id <历史用户ID>
```

工具会先将 `data/` 与 `backend/data/` 完整备份到 `backups/local-data-<UTC时间>/`，再写入根目录 `data/`。原始目录和备份均不会删除。

迁移范围包括指定用户拥有的会话、任务、学习事件、画像、资源包和资源，以及通过会话 ID 等关联键连接的记录。只有被迁移记录引用的产物会复制；课程知识库作为全局数据保留。

## 校验

迁移完成后至少检查：

```powershell
pytest -q backend/tests/services/migration/test_local_single_user.py
git status --short
```

然后核对：

- 根目录 `data/` 的所有用户字段仅为 `local-user`。
- 会话、任务、资源、事件和画像行数与源用户盘点一致。
- 被引用产物存在且哈希与备份一致。
- demo、competition、smoke 和其他用户未进入根目录 `data/`。
- 重启服务后，旧会话、资源 Viewer、画像和学习路径仍可读取。

如任何校验失败，保持服务停止，保留当前目录，并使用本次 `backups/local-data-*` 备份分析或恢复；不要删除 `backend/data/`。
