# 部署手册（同事照做即可）

信息价 / 清单定额档案台 + 爬虫控制台的部署步骤。目标：在一台 Windows 机器上把控制台跑起来，
并连上**共享的 PostgreSQL + MinIO**，看到和中心台一样的数据。

> 关键前提：数据（档案元数据在 PostgreSQL、原件文件在 MinIO）**不在代码仓库里**。
> 本机必须能访问同一套 PG 和 MinIO，才能看到真实数据。

---

## 0. 前置条件

- **Python 3.12+**（中心台用的是 3.14）
- **Git**
- 能访问共享的 **PostgreSQL**（当前公网入口端口 `5433`，库 `file_asset`）
- 能访问共享的 **MinIO**（默认端口 `9000`，桶 `cost-raw` 等）
- 向中心台负责人索取：PG 密码、MinIO access/secret、Basic Auth 账号密码

---

## 1. 克隆代码

```powershell
git clone https://github.com/tms424001/data-lake.git
cd data-lake
```

## 2. 创建虚拟环境并安装依赖

在**仓库根目录**执行：

```powershell
python -m venv file_asset_service\.venv
file_asset_service\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> `requirements.txt` 里的 `uvicorn[standard]` 会自动带入 `python-dotenv`（用于加载 `.env`），无需单独安装。

## 3. 配置 .env（在仓库根目录）

复制模板并填入真实值（此文件**不会进 git**，含密钥务必只留本地）：

```powershell
copy .env.example .env
notepad .env
```

至少填写以下几项（向中心台负责人索取真实值）：

```dotenv
# 共享 PostgreSQL（元数据）
FILE_ASSET_DATABASE_URL=postgresql+psycopg://file_asset:<password>@djtsoft.x3322.net:5433/file_asset

# 共享 MinIO（原件文件）
FILE_ASSET_S3_ENDPOINT_URL=http://<nas-host>:9000
FILE_ASSET_S3_ACCESS_KEY_ID=<access-key>
FILE_ASSET_S3_SECRET_ACCESS_KEY=<secret-key>
FILE_ASSET_S3_REGION_NAME=us-east-1

# 桶名（保持默认即可）
FILE_ASSET_RAW_BUCKET=cost-raw
FILE_ASSET_EXTRACT_BUCKET=cost-extract
FILE_ASSET_REPORT_BUCKET=cost-report

# 控制台监听地址/端口
FILE_ASSET_HOST=127.0.0.1
FILE_ASSET_PORT=8010

# 网页登录（Basic Auth）。留空则不需要登录
FILE_ASSET_BASIC_AUTH=<用户名>:<密码>

# worker 身份（每台机器必须唯一，若这台也跑采集）
FILE_ASSET_WORKER_ID=<本机唯一标识>
```

> 想让局域网同事也能访问这台控制台：把 `FILE_ASSET_HOST` 改为 `0.0.0.0`。

## 4. 启动

激活 venv 后，进入 `file_asset_service` 目录用 `serve.py` 启动
（`serve.py` 会自动加载根目录 `.env`、验证 NAS PostgreSQL 可达后再拉起 uvicorn）：

```powershell
cd file_asset_service
python serve.py
```

看到 `[serve] starting uvicorn on 127.0.0.1:8010` 即成功。

- 控制台首页：`http://127.0.0.1:8010/ui`
- 爬虫台：`http://127.0.0.1:8010/crawler`
- 健康检查：`http://127.0.0.1:8010/healthz`（免登录）

> ⚠️ **不要**裸跑 `uvicorn ...`。必须用 `serve.py`，使根目录 `.env` 在启动前加载。
> 缺少 NAS PostgreSQL / MinIO 配置、或数据库不可连接时，服务会直接报错并停止；不会回落到本地数据库或对象存储。

## 5.（可选）开机自启

把控制台注册为登录时自启的计划任务：

```powershell
python serve.py --install-task      # 创建并立即启动
python serve.py --uninstall-task    # 移除
```

## 6. 爬虫：一键增量全网 / 历史补爬 / 定时

### 6.1 页面手动触发（覆盖矩阵页 `/ui`）

- **一键增量全网**：覆盖矩阵右上角按钮。对所有已启用采集的信息价源跑一次增量抓取（各地最新期次），再自动执行下载。
- **补爬本年 / 补爬本月**：覆盖矩阵每年行「补爬本年」、每个未覆盖格子悬停出现的下载按钮「补爬本月」。按地区+期次回填历史数据（如北京 2024）。

> 增量 vs 补爬：增量只抓“最新一期”；历史空缺（如某地 2024 整年）必须用补爬显式指定期次。

### 6.2 Task + Loop 定时自动化（Windows 任务计划）

一次性 crawl cycle（调度 + 下载排空）脚本：`python -m app.crawl_cycle`

```powershell
# 增量：只跑到期的源
python -m app.crawl_cycle
# 强制：忽略到期判断，跑所有已启用源
python -m app.crawl_cycle --force
```

> `crawl_cycle` 仍可用于人工一次性增量。生产定时推荐使用 task + loop：循环程序只把到期站点入队，再排空数据库 worker 队列；站点的 `next_scan_at` 独立保存，不会因重新导入站点配置而丢失。

注册为每 2 小时运行一次 loop（增量月度发布窗口由每个站点策略决定）：

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"   # 换成你的 python
$svc = "D:\大匠通\新指标云\data_lake_handoff\data_lake_handoff\file_asset_service"    # 换成你的路径
schtasks /create /tn "cost-info-crawler-loop" `
  /tr "cmd /c cd /d `"$svc`" && `"$py`" -m app.crawler_loop run --once" `
  /sc hourly /mo 2 /st 00:10 /rl HIGHEST /f
# 立即测试一次
schtasks /run /tn "cost-info-crawler-loop"
# 移除
schtasks /delete /tn "cost-info-crawler-loop" /f
```

月度站点可在 `schedule_policy` 中配置实际发布窗口，例如：

```json
{
  "enabled": true,
  "frequency": "monthly",
  "timezone": "Asia/Shanghai",
  "scan_days": [1, 3, 5, 7, 10, 15],
  "scan_times": ["08:00", "14:00"]
}
```

### 6.3 首次全量采集活动

首次历史回填必须先创建活动；活动会生成一个来源任务，并在任务内循环处理发现的公告，同时把每条公告写入可审计的 `crawl_item` 清单。不要用 `--force` 代替历史回填。

```powershell
python -m app.crawl_campaign create `
  --source-id <source-id> `
  --name "四川德阳 2023-01 至 2026-07 首次回填" `
  --start-period 2023-01 `
  --end-period 2026-07

# 所有已验证、已启用且有适配器的站点：先预览，再创建一站点一活动
python -m app.crawl_campaign create-all --name-prefix "full-backfill-20260715" --dry-run
python -m app.crawl_campaign create-all --name-prefix "full-backfill-20260715"

# 查看活动进度（发现、完成、重复、失败）
python -m app.crawl_campaign list --source-id <source-id>
```

API 也可使用 `POST /api/crawler/campaigns` 创建活动、`GET /api/crawler/campaigns` 查询进度，`POST /api/crawler/loop/run` 可手动执行一个 loop round。

> 注意：只有 `active + 已启用定时 + 已配采集适配器` 的源才会进入增量循环。`create-all` 会按稳定站点 ID 去重，防止重复来源产生两条档案谱系；全量活动还要求该站点的解析器分页范围已覆盖目标历史期次。先以小范围期次验证后再放开全量。

---

## 日常同步（拉取中心台的最新迭代）

```powershell
git pull
# 若依赖有更新：
file_asset_service\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
# 重启服务：结束旧的 python serve.py 进程后重新 python serve.py
```

---

## 常见问题

- **页面数字为空 / 看不到档案**：多半是 `.env` 没连上共享 PG/MinIO，或裸跑了 uvicorn。检查 `FILE_ASSET_DATABASE_URL`、`FILE_ASSET_S3_ENDPOINT_URL` 是否指向共享服务。
- **`[serve] configuration error`**：缺少 NAS 的连接串或对象存储凭据，检查根目录 `.env`。
- **`[serve] database unavailable`**：本机连不到 NAS PostgreSQL；检查网络、VPN、端口、数据库服务状态和密码。修复后重新启动服务。
- **端口被占用**：改 `.env` 的 `FILE_ASSET_PORT`，或结束占用 `8010` 的进程。
- **日志**：`file_asset_service/console_service.log`。

更详细的运行说明见 `docs/windows_startup_guide.md`。
