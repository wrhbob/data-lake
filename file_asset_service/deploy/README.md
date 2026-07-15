# Shared NAS services and crawler node deployment

## NAS host

The NAS PostgreSQL instance is managed outside this repository and is the only
shared catalog database. The Compose file does not create a database; it may be
used only for the NAS MinIO data-lake service.

```bash
cd deploy/nas
cp .env.example .env
# edit MinIO credentials and bind addresses
docker compose up -d minio
```

Initialize or migrate the catalog schema once from any machine that can reach the NAS PostgreSQL:

```bash
export FILE_ASSET_DATABASE_URL='postgresql+psycopg://file_asset:<password>@djtsoft.x3322.net:5433/file_asset'
python - <<'PY'
from app.database import init_db
init_db()
print("schema initialized")
PY
```

## Crawler node

Crawler nodes do not need local PostgreSQL. Each node points to the NAS PostgreSQL and NAS MinIO endpoint.

```bash
cp deploy/crawler-node.env.example .env.crawler-node
# edit host, credentials, and unique FILE_ASSET_WORKER_ID per machine
set -a
. ./.env.crawler-node
set +a

python -m app.cost_info_worker run \
  --limit 3 \
  --worker-id "$FILE_ASSET_WORKER_ID" \
  --lease-seconds "$FILE_ASSET_WORKER_LEASE_SECONDS" \
  --trigger crawler_node
```

Worker leasing is database-backed:

- Workers atomically change due tasks from `pending` to `running`.
- PostgreSQL uses `FOR UPDATE SKIP LOCKED`, so parallel crawler nodes do not take the same task.
- `worker_id`, `lease_expires_at`, and `heartbeat_at` are written to `collection_task`.
- Expired `running` leases can be reclaimed by another worker.

## 招投标公告 task + loop

招投标来源使用同一套共享 PostgreSQL 任务队列，但由独立的
`trading_task_loop` 处理，避免与信息价适配器互相影响。首次接入时先登记来源，
再对指定来源做一次受控验证；只有验证成功的来源会被日常 loop 调度。

```bash
# 仅首次或新增内置来源时执行：来源会以 pending_verify 状态登记
python -m app.trading_task_loop register

# 已验证来源的常态运行：建议以独立进程/服务常驻，每 15 分钟一轮
python -m app.trading_task_loop run \
  --interval-seconds 900 \
  --worker-id "$FILE_ASSET_WORKER_ID" \
  --lease-seconds "$FILE_ASSET_WORKER_LEASE_SECONDS"
```

各来源在自身配置中声明北京时间的扫描时段；每个来源同一时刻最多一个任务，数据库
租约保证多节点不会重复执行。四川省工程建设源 `trading.scggzy.jsgc` 按 22 个官方
来源代码分别维护通道（含省级及 21 个地市州），每日 09:25、13:25、17:25 扫描；公告
正文先入湖，城市附件链接以待下载文件归档。初次验证可显式限制为一个频道、一页、一条：

```bash
python -m app.trading_task_loop run --once --verify --force \
  --site-id trading.cdggzy.jsgc --channel-id tender_notice \
  --max-pages 1 --page-size 1 --max-items-per-channel 1
```

四川源的四市验证集可使用：

```bash
python -m app.trading_task_loop run --once --verify --force \
  --site-id trading.scggzy.jsgc \
  --channel-id tender_notice_s003 --channel-id tender_notice_s004 \
  --channel-id tender_notice_s011 --channel-id tender_notice_s013
```
