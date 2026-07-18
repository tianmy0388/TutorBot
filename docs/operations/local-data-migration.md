# 本地历史数据迁移与恢复

本文用于把历史上的两个本地数据根目录安全汇总到仓库根目录的 `data/`：

- 规范目录：`<repo>/data`
- 旧目录：`<repo>/backend/data`

迁移目标用户固定为 `local-user`。迁移程序会先把两个源目录完整备份到
`<repo>/backups/local-data-<UTC 时间戳>/`，再合并 SQLite 数据库和缺失的资源文件；
源目录不会被删除。SQLite 备份使用数据库 backup API，因此会包含已提交的 WAL 数据。

> 在迁移窗口内停止 TutorBot 后端及其他可能写入这些 SQLite 文件的进程。不要手工删除
> `data/`、`backend/data/` 或备份。所有命令均在 PowerShell 中执行，且不会输出 API key。

## 1. 初始化审计环境

以下命令以本项目使用的本地 Conda `tutor` 环境为准。`migration-audit/` 只保存清单与命令输出，
不参与运行时数据加载。

```powershell
$Repo = 'E:\github\TutorBot'
$Python = 'E:\Anaconda3\anaconda\envs\tutor\python.exe'
$env:PYTHONPATH = Join-Path $Repo 'backend'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$AuditDir = Join-Path $Repo "migration-audit\$Stamp"
New-Item -ItemType Directory -Path $AuditDir -Force | Out-Null

$SourceDirs = @(
  (Join-Path $Repo 'data'),
  (Join-Path $Repo 'backend\data')
) | Where-Object { Test-Path -LiteralPath $_ }
$SourceDirsBefore = @($SourceDirs)
```

用下面的只读函数记录每个 SQLite 数据库的逐表行数，以及每个数据根目录的 artifact 数。
这里的 artifact 指排除 `.db/.sqlite/.sqlite3` 与 `-wal/-shm/-journal` 后的文件。

```powershell
function Save-DataInventory {
  param(
    [Parameter(Mandatory = $true)][string[]]$Roots,
    [Parameter(Mandatory = $true)][string]$OutputPath
  )

  $env:TUTOR_INVENTORY_ROOTS = ($Roots -join [IO.Path]::PathSeparator)
  @'
import json
import os
import sqlite3
from pathlib import Path

db_suffixes = {".db", ".sqlite", ".sqlite3"}
sidecars = ("-wal", "-shm", "-journal")
result = {"roots": {}}

for raw_root in filter(None, os.environ["TUTOR_INVENTORY_ROOTS"].split(os.pathsep)):
    root = Path(raw_root).resolve()
    entry = {"exists": root.is_dir(), "artifact_files": 0, "databases": {}}
    result["roots"][str(root)] = entry
    if not root.is_dir():
        continue

    files = [path for path in root.rglob("*") if path.is_file()]
    entry["artifact_files"] = sum(
        path.suffix.lower() not in db_suffixes
        and not path.name.lower().endswith(sidecars)
        for path in files
    )

    for database in sorted(path for path in files if path.suffix.lower() in db_suffixes):
        relative = database.relative_to(root).as_posix()
        try:
            connection = sqlite3.connect(
                f"{database.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            with connection:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                table_rows = {
                    table: connection.execute(
                        f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                    ).fetchone()[0]
                    for table in tables
                }
                ownership = {}
                for table in tables:
                    escaped_table = table.replace(chr(34), chr(34) * 2)
                    columns = {
                        row[1]
                        for row in connection.execute(f'PRAGMA table_info("{escaped_table}")')
                    }
                    for column in ("user_id", "owner_user_id"):
                        if column not in columns:
                            continue
                        ownership[f"{table}.{column}"] = [
                            row[0]
                            for row in connection.execute(
                                f'SELECT DISTINCT "{column}" FROM "{escaped_table}" '
                                f'WHERE "{column}" IS NOT NULL ORDER BY "{column}"'
                            )
                        ]
                entry["databases"][relative] = {
                    "table_rows": table_rows,
                    "ownership": ownership,
                }
            connection.close()
        except sqlite3.Error as exc:
            entry["databases"][relative] = {"error_type": type(exc).__name__}

print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
'@ | & $Python - | Set-Content -LiteralPath $OutputPath -Encoding utf8
  if ($LASTEXITCODE -ne 0) {
    throw "inventory failed with exit code $LASTEXITCODE"
  }
}

Save-DataInventory -Roots $SourceDirsBefore -OutputPath (Join-Path $AuditDir 'before.json')
```

## 2. Dry-run

Dry-run 只发现源目录和历史用户，不创建备份、不写文件。确认输出中的 `source:` 同时覆盖实际存在的
两个目录，`target:` 为 `<repo>/data`，且 `backup: (none)`、`written_files: 0`。

```powershell
$DryRunOutput = & $Python -m tutor.cli.main migrate-local-data `
  --repo-root $Repo `
  --target-user-id local-user `
  --relocate-from 'E:\github\Tutor' `
  --dry-run 2>&1
$DryRunOutput | Tee-Object -FilePath (Join-Path $AuditDir 'dry-run.txt')
if ($LASTEXITCODE -ne 0) {
  throw "migration dry-run failed with exit code $LASTEXITCODE"
}
```

`--relocate-from` 必须是人工确认过的旧仓库根目录，可重复指定。迁移器只会处理位于该根目录的
`data/` 或 `backend/data/` 下、原绝对路径已不存在且同一相对文件在当前数据根真实存在的记录。
仍然存在的外部文件永不重定向；未显式允许的路径会保留原值并打印为 `unresolved_path:`。

## 3. 执行迁移并验证备份

```powershell
$MigrationOutput = & $Python -m tutor.cli.main migrate-local-data `
  --repo-root $Repo `
  --target-user-id local-user `
  --relocate-from 'E:\github\Tutor' 2>&1
$MigrationOutput | Tee-Object -FilePath (Join-Path $AuditDir 'migration.txt')
if ($LASTEXITCODE -ne 0) {
  throw "migration failed with exit code $LASTEXITCODE"
}

$BackupLine = $MigrationOutput |
  ForEach-Object { "$_" } |
  Where-Object { $_ -match '^backup:\s+' } |
  Select-Object -Last 1
$Backup = ($BackupLine -replace '^backup:\s*', '').Trim()
if (-not $Backup -or $Backup -eq '(none)' -or -not (Test-Path -LiteralPath $Backup)) {
  throw 'migration did not produce a verifiable backup directory'
}
$Backup | Set-Content -LiteralPath (Join-Path $AuditDir 'backup-path.txt') -Encoding utf8

foreach ($SourceDir in $SourceDirsBefore) {
  if (-not (Test-Path -LiteralPath $SourceDir)) {
    throw "source directory was removed unexpectedly: $SourceDir"
  }
}

Save-DataInventory `
  -Roots @((Join-Path $Repo 'data'), (Join-Path $Repo 'backend\data')) `
  -OutputPath (Join-Path $AuditDir 'after.json')
Save-DataInventory `
  -Roots @($Backup) `
  -OutputPath (Join-Path $AuditDir 'backup.json')

function Get-ArtifactCount {
  param([object]$Inventory, [string]$Root)
  $Resolved = [IO.Path]::GetFullPath($Root).TrimEnd('\')
  $Property = $Inventory.roots.PSObject.Properties |
    Where-Object { [IO.Path]::GetFullPath($_.Name).TrimEnd('\') -eq $Resolved } |
    Select-Object -First 1
  if ($null -eq $Property) { return 0 }
  return [int]$Property.Value.artifact_files
}

$BeforeInventory = Get-Content -Raw (Join-Path $AuditDir 'before.json') | ConvertFrom-Json
$AfterInventory = Get-Content -Raw (Join-Path $AuditDir 'after.json') | ConvertFrom-Json
$CanonicalData = Join-Path $Repo 'data'
$ArtifactCopies = (Get-ArtifactCount $AfterInventory $CanonicalData) -
  (Get-ArtifactCount $BeforeInventory $CanonicalData)
if ($ArtifactCopies -lt 0) { throw 'canonical artifact count decreased unexpectedly' }
$ArtifactCopies | Set-Content -LiteralPath (Join-Path $AuditDir 'artifact-copies.txt') -Encoding utf8
```

检查 `before.json`、`after.json` 和 `backup.json`：

- `backup.json` 应保留迁移前两个源目录的数据库行数和 artifact 数；
- `after.json` 中根 `data/` 应包含合并后的记录，其 `ownership` 中所有非空值都应为
  `local-user`；
- `backend/data/` 仍存在且内容未被迁移器改写；
- `migration.txt` 中的 `written_files` 是发生创建或修改的规范目标路径数，不能代替 artifact 数；
- `artifact-copies.txt` 是规范 `data/` 的 artifact 增量，也就是本次实际复制的新 artifact 数；
- 冲突的同路径非数据库文件不会覆盖规范副本，两个源副本均保留供人工处理。

### 实际运行记录

实际执行后，把本次审计目录中的值填写到下表并随变更记录保留：

| 项目 | 实际值 |
|---|---|
| 执行时间 | 2026-07-18 19:22:34 CST；路径修正重跑 19:24:49 CST；嵌套 owner 修正重跑 20:55:57 CST；真实 E2E 完成 20:59:07 CST |
| 审计记录 | 本表；独立恢复目录 `E:\github\TutorBot\backups\recovery-verification-20260718T112234974218Z` |
| CLI 打印的备份目录 | 迁移前：`E:\github\TutorBot\backups\local-data-20260718T112234974218Z`；路径修正前：`E:\github\TutorBot\backups\local-data-20260718T112449769678Z`；嵌套 owner 修正前：`E:\github\TutorBot\backups\local-data-20260718T125557858581Z` |
| dry-run 发现的源目录 | `E:\github\TutorBot\data`、`E:\github\TutorBot\backend\data` |
| dry-run 发现的用户 | 34 个历史 ID（8 个命名测试/本地 ID，26 个 `u_*` ID） |
| `written_files` | 首次 44；路径修正重跑 2；嵌套 JSON owner 修正重跑 4 |
| `unresolved_path` | 首次发现 3 条旧 `E:\github\Tutor\backend\data\code_runs\...\figure_1.png`；确认文件存在后完成重定位，最终为 none。独立审查后自动后缀猜测已移除，当前代码要求显式 `--relocate-from E:\github\Tutor`；现有备份保留了修正前记录 |
| 迁移前数据库逐表计数 | 规范 `profiles.db`: `profiles=0, profile_events=0`；旧目录：`conversations=25, messages=34, jobs=41, courses=1, documents=4, knowledge_bases=5, schema_meta=1, learning_events=1, profiles=34, profile_events=70, resource_packages=34, resources=135` |
| 迁移与真实 E2E 后逐表计数 | `conversations=25, messages=34, jobs=47, courses=1, documents=4, knowledge_bases=5, schema_meta=1, learning_events=11, profiles=1, profile_events=72, learning_paths=2, resource_packages=34, resources=135`；活动任务为 0；SQL ownership 和嵌套 JSON `user_id/owner_user_id` 均为 `local-user` |
| 迁移前/后 artifact 数 | 规范目录 `0 → 38`；旧目录保持 38，46 个源文件的 SHA-256 清单零变化 |
| 实际复制 artifact 数 | 38 |
| 历史会话 | `sess_ebb5a8f5dfdb`: conversation 1、message 1、job 10、package 3；历史运行中任务均修复为失败终态，真实 E2E 新任务也均进入终态 |
| 画像与路径 | `local-user` 画像 v3、event watermark 11；画像 v2/v3 各有一条 4 节点路径。原 v2 失败任务保留审计历史，确定性的 `recovery-1` 成功补建，后续 5 个事件正常生成 v3 |
| 重启指纹 | 两次启动均恢复 conversation、message、package、profile 与 path；最终 `running_jobs=0`，34/34 个资源包 list/detail 均返回可读结果，公开投影不包含原始 traceback |
| 恢复演练 | 迁移前备份恢复出 45 文件/43,056,441 字节；恢复副本 dry-run 与写迁移通过，旧目录哈希零变化，owner=`local-user`、messages=1、jobs=4 |

## 4. 验证历史会话 `sess_ebb5a8f5dfdb`

先直接检查规范 SQLite 数据。脚本会列出所有包含 `session_id` 列的表，并强制校验 conversation 所有者
和消息数量。

```powershell
$env:TUTOR_VERIFY_REPO = $Repo
@'
import json
import os
import sqlite3
from pathlib import Path

root = Path(os.environ["TUTOR_VERIFY_REPO"]).resolve()
data = root / "data"
session_id = "sess_ebb5a8f5dfdb"
conversation_db = data / "conversations.db"
if not conversation_db.is_file():
    raise SystemExit(f"missing {conversation_db}")

with sqlite3.connect(f"{conversation_db.as_uri()}?mode=ro", uri=True) as connection:
    row = connection.execute(
        "SELECT session_id, user_id, message_count, web_search_enabled "
        "FROM conversations WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    message_rows = connection.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]

if row is None:
    raise SystemExit(f"missing session {session_id}")
if row[1] != "local-user":
    raise SystemExit(f"unexpected owner: {row[1]}")
if message_rows == 0:
    raise SystemExit("session exists but has no persisted messages")

counts = {}
for database in sorted(data.rglob("*.db")):
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            columns = {item[1] for item in connection.execute(f'PRAGMA table_info("{table}")')}
            if "session_id" in columns:
                count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE session_id = ?',
                    (session_id,),
                ).fetchone()[0]
                counts[f"{database.name}:{table}"] = count

print(json.dumps({
    "conversation": {
        "session_id": row[0],
        "user_id": row[1],
        "declared_message_count": row[2],
        "message_rows": message_rows,
        "web_search_enabled": bool(row[3]),
    },
    "session_rows": counts,
}, ensure_ascii=False, indent=2, sort_keys=True))
'@ | & $Python - | Tee-Object -FilePath (Join-Path $AuditDir 'session-db.json')
if ($LASTEXITCODE -ne 0) {
  throw "session database verification failed with exit code $LASTEXITCODE"
}
```

再启动后端，通过前端实际使用的 aggregate 接口确认对话、任务、资源包、画像摘要与学习路径摘要可恢复：

```powershell
Set-Location $Repo
$env:PYTHONPATH = Join-Path $Repo 'backend'
& $Python -m tutor.cli.main api
```

在另一个 PowerShell 窗口执行：

```powershell
$Repo = 'E:\github\TutorBot'
$SessionId = 'sess_ebb5a8f5dfdb'
$Uri = "http://localhost:18000/api/v1/conversations/$SessionId/aggregate?user_id=local-user"
$Aggregate = Invoke-RestMethod -Method Get -Uri $Uri

if ($Aggregate.conversation.session_id -ne $SessionId) { throw 'wrong session returned' }
if ($Aggregate.conversation.user_id -ne 'local-user') { throw 'wrong owner returned' }
if (@($Aggregate.conversation.messages).Count -eq 0) { throw 'messages were not restored' }
if (@($Aggregate.jobs).Count -eq 0) { throw 'jobs were not restored' }
if (@($Aggregate.packages).Count -eq 0) { throw 'resource packages were not restored' }

$Fingerprint = [pscustomobject]@{
  session_id = $Aggregate.conversation.session_id
  owner = $Aggregate.conversation.user_id
  messages = @($Aggregate.conversation.messages).Count
  jobs = @($Aggregate.jobs).Count
  packages = @($Aggregate.packages).Count
  profile_keys = @($Aggregate.profile_summary.PSObject.Properties).Count
  path_keys = @($Aggregate.path_summary.PSObject.Properties).Count
  recovery_warnings = @($Aggregate.recovery_warnings).Count
}
$Fingerprint | ConvertTo-Json | Tee-Object -FilePath (Join-Path $Repo 'migration-audit\aggregate-before-restart.json')
```

停止并重新启动后端，再执行同一 aggregate 请求，把指纹保存为 `aggregate-after-restart.json`，然后比较：

```powershell
$AfterRestart = Invoke-RestMethod -Method Get -Uri $Uri
$AfterFingerprint = [pscustomobject]@{
  session_id = $AfterRestart.conversation.session_id
  owner = $AfterRestart.conversation.user_id
  messages = @($AfterRestart.conversation.messages).Count
  jobs = @($AfterRestart.jobs).Count
  packages = @($AfterRestart.packages).Count
  profile_keys = @($AfterRestart.profile_summary.PSObject.Properties).Count
  path_keys = @($AfterRestart.path_summary.PSObject.Properties).Count
  recovery_warnings = @($AfterRestart.recovery_warnings).Count
}
$AfterFingerprint | ConvertTo-Json |
  Tee-Object -FilePath (Join-Path $Repo 'migration-audit\aggregate-after-restart.json')

$Difference = Compare-Object `
  ($Fingerprint.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) `
  ($AfterFingerprint.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" })
if ($Difference) {
  $Difference | Format-Table
  throw 'aggregate changed across backend restart'
}
```

浏览器端还应确认：刷新页面后对话、资源卡、任务终态、画像和路径不消失；缺失 artifact 应显示可恢复警告，
而不是让整个 aggregate 请求失败。

## 5. 在独立目录演练恢复

以下流程只把备份恢复到新的 `recovery/` 目录，不覆盖当前 `data/`。这既是回滚验证，也是备份可读性验证。

```powershell
$Recovery = Join-Path $Repo "recovery\local-data-$Stamp"
New-Item -ItemType Directory -Path $Recovery -Force | Out-Null

foreach ($Relative in @('data', 'backend\data')) {
  $From = Join-Path $Backup $Relative
  if (-not (Test-Path -LiteralPath $From)) { continue }
  $To = Join-Path $Recovery $Relative
  New-Item -ItemType Directory -Path (Split-Path $To -Parent) -Force | Out-Null
  Copy-Item -LiteralPath $From -Destination $To -Recurse
}

& $Python -m tutor.cli.main migrate-local-data `
  --repo-root $Recovery `
  --target-user-id local-user `
  --relocate-from 'E:\github\Tutor' `
  --dry-run
if ($LASTEXITCODE -ne 0) { throw 'recovery dry-run failed' }

# 只合并 recovery 内的两个副本；不会改动生产 data/ 或 backend/data/。
& $Python -m tutor.cli.main migrate-local-data `
  --repo-root $Recovery `
  --target-user-id local-user `
  --relocate-from 'E:\github\Tutor'
if ($LASTEXITCODE -ne 0) { throw 'recovery rehearsal migration failed' }

Save-DataInventory `
  -Roots @((Join-Path $Recovery 'data'), (Join-Path $Recovery 'backend\data')) `
  -OutputPath (Join-Path $AuditDir 'recovery.json')
```

如需启动只读式人工验收环境，先停止当前后端，再临时把运行数据目录指向恢复副本：

```powershell
$env:TUTOR_DATA_DIR = Join-Path $Recovery 'data'
$env:PYTHONPATH = Join-Path $Repo 'backend'
Set-Location $Repo
& $Python -m tutor.cli.main api
```

验收结束后关闭该进程并执行 `Remove-Item Env:TUTOR_DATA_DIR`。不要把 recovery 目录直接复制回生产目录；
真正切回旧数据前应先停止服务、再为当前生产数据做一份新的独立备份，并保留本次审计记录。

## 6. 完整验收命令

```powershell
$Repo = 'E:\github\TutorBot'
$Python = 'E:\Anaconda3\anaconda\envs\tutor\python.exe'
$env:PYTHONPATH = Join-Path $Repo 'backend'
Set-Location $Repo

& $Python -m pytest backend/tests -q
if ($LASTEXITCODE -ne 0) { throw 'backend tests failed' }

npm --prefix frontend run type-check
if ($LASTEXITCODE -ne 0) { throw 'frontend type-check failed' }
npm --prefix frontend run lint
if ($LASTEXITCODE -ne 0) { throw 'frontend lint failed' }
npm --prefix frontend test
if ($LASTEXITCODE -ne 0) { throw 'frontend tests failed' }
npm --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }

$env:TUTOR_E2E_REAL_DATA = '1'
npm --prefix frontend run test:e2e
if ($LASTEXITCODE -ne 0) { throw 'real-data E2E failed' }
Remove-Item Env:TUTOR_E2E_REAL_DATA
```

首次运行 Playwright 且本机没有 Chromium 缓存时，先执行
`npm exec --workspace frontend playwright install chromium`。本次真实数据验收结果为 12 passed、2 expected
skipped：历史代码题资源本身没有 `code_spec`，以及 MiniMax 在线调用受显式环境开关保护；确定性代码题 fixture
已覆盖 `.py` 上传、运行、提交与刷新恢复。

核心浏览器流程需在桌面视口和 390×844 移动视口各通过一次，包括历史会话恢复、图片放大、代码题
上传/提交与刷新恢复、Manim 成功和失败终态、画像/路径，以及联网搜索默认关闭和会话级持久化。

## 7. MiniMax MCP 联网搜索验收

服务端联网搜索是全局许可与会话开关的双重门：运行时 `TUTOR_WEB_SEARCH_ENABLED` 必须允许搜索，聊天框中
每个会话的“联网搜索”开关才会生效。新建会话的开关默认关闭；选择会写入 conversation，刷新或重启后
按该会话恢复，任务开始时还会固化为不可变快照。

本项目使用 MiniMax MCP 时，只配置下列非敏感项：

```dotenv
TUTOR_WEB_SEARCH_ENABLED=true
TUTOR_WEB_SEARCH_PROVIDER=mcp
TUTOR_WEB_SEARCH_MCP_SERVER=MiniMax
TUTOR_WEB_SEARCH_MCP_TOOL=web_search
```

MiniMax 凭据只保存在本机环境或 `.mcp.json` 所引用的环境变量中；不要把凭据写入本文、提交到 Git、
粘贴到测试输出或截图。验收时创建两个会话：一个保持关闭并确认零搜索来源，另一个显式开启并确认响应
或资源 metadata 包含 HTTP(S) 来源；刷新后两个会话应各自保持原选择。MCP 超时或不可用时，主任务应以
明确的 `WEB_SEARCH_UNAVAILABLE` 降级信息继续，而不是泄漏 provider 原始错误或永久停在运行中。

显式在线验收使用以下命令；它会校验服务端实际暴露的非敏感配置严格为
`provider=mcp, mcp_server=MiniMax, mcp_tool=web_search`，并验证最新终态事件包含
`search_used=true` 和 HTTP(S) 来源：

```powershell
$env:TUTOR_E2E_REAL_DATA = '1'
$env:TUTOR_E2E_MINIMAX_SEARCH = '1'
npm --prefix frontend run test:e2e -- --grep "configured MiniMax MCP"
if ($LASTEXITCODE -ne 0) { throw 'MiniMax MCP E2E failed' }
Remove-Item Env:TUTOR_E2E_MINIMAX_SEARCH
Remove-Item Env:TUTOR_E2E_REAL_DATA
```

本次在当前本地 MiniMax MCP 配置上的结果为 1 passed（约 1 分钟）；测试结束后清除了临时会话和任务，
未输出或持久化任何凭据。
