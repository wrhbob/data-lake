# Windows 调度台启动规范（本机）

本机（Windows）运行「信息价 / 定额档案调度台」的**唯一正确启动方式**与硬性约束。

## 一、铁律

1. **必须用 `serve.py` 启动**，它会加载 `.env`，连到：
   - 元数据库：**docker Postgres** `127.0.0.1:15432/file_asset`
   - 对象存储：**NAS MinIO** `http://djtsoft.x3322.net:9000`（`cost-raw` / `cost-extract` / `cost-report`）
2. **禁止**用 `FILE_ASSET_DATABASE_URL=sqlite:///data/file_asset.db` 直接起 `uvicorn`。
   SQLite 是一个几乎为空的本地库，会让页面「只看到 0/几份」，并覆盖 8010 端口。
3. **禁止**裸跑 `python -m uvicorn app.main:create_app ...`（不加载 `.env` → 回落 SQLite + 本地 9000，写入必失败）。
4. 依赖前提：**Docker Desktop 已启动**（Postgres 容器 `restart=always`）。

## 二、标准启动步骤

```powershell
# 1) 确保 Docker 引擎在线（postgres 容器随之恢复）
docker info                     # 报错则先启动 Docker Desktop
docker compose -f file_asset_service\docker-compose.yml up -d postgres

# 2) 用 serve.py 启动调度台（加载 .env）
python file_asset_service\serve.py
```

- 解释器用系统 Python：`C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe`
- 注意：`file_asset_service\.venv` 已损坏（指向不存在的 anaconda），**不要用它**。
- 默认地址：`http://127.0.0.1:8010`，健康检查：`http://127.0.0.1:8010/healthz`

## 三、开机自启（已固化为计划任务）

已创建两个 onlogon 计划任务：

| 任务名 | 作用 |
| --- | --- |
| `docker-autostart` | 登录时启动 Docker Desktop（拉起 postgres 容器） |
| `file-asset-console` | 登录时用系统 Python 运行 `serve.py`（加载 .env，等待 DB 就绪后起 uvicorn） |

常用管理命令：

```powershell
schtasks /query /tn "file-asset-console"      # 查看
schtasks /run   /tn "file-asset-console"      # 立即运行
schtasks /end   /tn "file-asset-console"      # 停止本次
schtasks /delete /tn "file-asset-console" /f  # 卸载
```

运行日志：`file_asset_service\console_service.log`

## 四、启动后自检

```powershell
# 应返回 postgres 连接串（不是 sqlite），以及 NAS MinIO endpoint
python -c "import psutil; p=[x for x in psutil.process_iter() if 'uvicorn' in ' '.join(x.cmdline())][0]; print({k:v for k,v in p.environ().items() if 'FILE_ASSET_DATABASE_URL' in k or 'S3_ENDPOINT' in k})"

# 档案数应为数百（非 0/个位数）
docker exec file_asset_service-postgres-1 psql -U file_asset -d file_asset -c "SELECT count(*) FROM archive WHERE domain_type='cost_info';"
```

若档案数异常偏小或环境变量是 `sqlite:///...`，说明被错误方式启动过，按第二节用 `serve.py` 重启。

## 五、历史原件回补（一次性/按需）

NAS 原件若已编目但未入档，用 manifest 回补（引用现有 NAS 对象，不重爬、不重传）：

```powershell
# 先试跑 20 行验证
python -c "from dotenv import load_dotenv; load_dotenv(r'.env'); import sys,runpy; sys.argv=['rebuild','manifests/latest/parse_manifest.csv','--max-rows','20']; runpy.run_module('app.manifest_catalog_rebuild', run_name='__main__')"
# 全量（去掉 --max-rows）；须在 file_asset_service 目录下、或相应调整相对路径
```

## 六、多机部署模型：中心台单点（强烈推荐）

目标：**无论哪台电脑、内网还是外网，爬虫采集与手动上传都必须落到 NAS，且经过 MinIO。**

### 6.1 存储通道（代码事实）

- 手动上传 `POST /api/file-assets/ingest`、爬虫采集，两条路都收敛到
  `register_asset()` → `storage.put_object()` → `S3ObjectStore`，写的是
  `FILE_ASSET_S3_ENDPOINT_URL` 指向的 MinIO。
- **没有本地磁盘写入旁路**（`FakeObjectStore` 仅测试用）。
- 因此"是否落 NAS"完全由**服务实例的 `.env`** 决定；默认值会回落到本地
  `127.0.0.1:9000`（`config.py`），一旦漏配就不进 NAS。

### 6.2 推荐模型：所有人只用一个中心台网址

- **同事只通过你给的网址访问中心台**（本机 `8010`，`0.0.0.0` 对内外网开放），
  做手动上传、以及在页面上触发采集。
- 浏览器/HTTP 只把文件交给中心台，真正写 MinIO 用的是**中心台的 `.env`**，
  与同事电脑本地配置无关 → **必然经 MinIO 落 NAS**。
- 爬虫也**只在中心台这台机器上跑**（计划任务 / 页面触发 / 本机 `cost_info_worker`）。
- 这样只需维护**这一台的 `.env`**，下面三条硬约束即自动满足。

### 6.3 三条硬约束

1. **禁默认回落**：任何服务/worker 实例启动前必须加载本 `.env`（用 `serve.py`
   或 `run_worker.sh`）。严禁裸跑 `uvicorn` / `cost_info_worker` 而不带
   `FILE_ASSET_S3_ENDPOINT_URL` 与 `FILE_ASSET_DATABASE_URL`，否则回落本地
   `127.0.0.1:9000` + SQLite，数据不进 NAS。
2. **统一端点/中心台**：要么全员共用中心台网址（推荐），要么**每台独立跑
   worker 的机器** `.env` 都显式指向 NAS MinIO 端点 + 账号
   （账号/密钥见私密 `.env`，勿写入版本库）。独立 worker 直连 MinIO/DB，**不经过网址**，
   光给网址不够。
3. **修好 NAS Postgres 统一元数据**：文件进同一 MinIO 后，元数据也必须进同一库。
   当前元数据在本机 docker Postgres(`15432`)；多机独立 worker 会导致元数据分散，
   除非全部连同一个库（即 NAS Postgres `172.16.20.26:5433`，**目前不可用，待修复**）。
   在 NAS Postgres 修好前，坚持 6.2 的"中心台单点"即可保证元数据统一。

### 6.4 独立 worker 机器的 .env 最小要求（仅当不走中心台单点时）

```dotenv
FILE_ASSET_S3_ENDPOINT_URL=http://djtsoft.x3322.net:9000
FILE_ASSET_S3_ACCESS_KEY_ID=<minio-access-key>
FILE_ASSET_S3_SECRET_ACCESS_KEY=<minio-secret-key>
FILE_ASSET_DATABASE_URL=<统一的 NAS Postgres 连接串>   # 修好后填
FILE_ASSET_WORKER_ID=<每台唯一>                         # 多机去重靠它
```

## 七、网页登录（Basic Auth）

- 已在 `.env` 启用：`FILE_ASSET_BASIC_AUTH=<用户名>:<密码>`
  （具体账号/密码见私密 `.env`，勿写入版本库）。
- 生效范围：所有 `8010` 访问均需登录；`/healthz` 豁免（供健康检查/计划任务）。
- 由 `app/main.py` 的 `BasicAuthMiddleware` 在启动时读取该变量装配；
  **改动后必须重启服务**（`serve.py`）才生效。
- 改密码：编辑 `.env` 的 `FILE_ASSET_BASIC_AUTH=用户名:密码` 后重启。
- 关闭登录：置空或注释该行后重启。
