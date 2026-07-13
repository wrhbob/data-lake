# 数据湖交付包

这个文件夹是给同事独立安装和使用的数据湖/信息价爬虫交付包。

它不依赖 `/Users/tms/Desktop/cloudSchool` 大仓库。目录内的 `file_asset_service/` 是可安装 Python 包，`manifests/latest/` 是当前导出的清单样例和交接文件。

## 目录说明

```text
data_lake_handoff/
  .env.example                         环境变量模板，不含真实密码
  README_START_HERE.md                 从这里开始
  file_asset_service/                  可安装 Python 包
    app/                               API、爬虫 worker、manifest 导出、数据湖 catalog 代码
    data/                              全国信息价源和行政区划映射
    deploy/                            NAS Docker Compose 与 crawler node env 模板
    tests/                             回归测试
    pyproject.toml                     Python 依赖
    uv.lock                            uv 锁文件
  manifests/latest/                    当前导出的 manifest
  scripts/                             安装、启动、导出、发布脚本
  docs/data_lake_handoff_plan.md       NAS 数据湖交接落地方案
```

## 快速安装

```bash
cd data_lake_handoff
./scripts/install.sh
cp .env.example .env
```

编辑 `.env`，填入 NAS PostgreSQL、MinIO endpoint 和账号。

## 初始化或迁移数据库

只需要在 NAS PostgreSQL 上执行一次：

```bash
./scripts/init_db.sh
```

## 启动调度台

```bash
./scripts/start_console.sh
```

默认地址：

```text
http://127.0.0.1:8010/crawler
```

## 运行 worker

每台爬虫机器必须设置不同的 `FILE_ASSET_WORKER_ID`。

```bash
./scripts/run_worker.sh
```

默认一次最多领取 3 个任务。多台机器同时跑时，PostgreSQL `FOR UPDATE SKIP LOCKED` 会防止重复领取任务。

## 导出 manifest

```bash
./scripts/export_manifest.sh
```

输出到：

```text
manifests/latest/parse_manifest.csv
manifests/latest/parse_manifest.jsonl
manifests/latest/parse_manifest_cost_info.csv
manifests/latest/parse_manifest_cost_info.jsonl
```

## 发布 manifest 到 MinIO

需要机器上安装 MinIO Client `mc`，并且 `.env` 里配置的账号有 `cost-report` 写权限。

```bash
./scripts/publish_manifest_minio.sh
```

发布后的固定读取位置：

```text
s3://cost-report/manifests/latest/parse_manifest_cost_info.csv
s3://cost-report/manifests/latest/parse_manifest_cost_info.jsonl
```

## 同事最常用入口

解析加工同事优先看：

```text
manifests/latest/parse_manifest_cost_info.csv
```

生产环境建议改为从 MinIO 读取：

```text
s3://cost-report/manifests/latest/parse_manifest_cost_info.csv
```

每一行对应一个可解析原始文件，核心字段包括：

```text
object_key
sha256
original_names
resolved_regions
region_codes
period_starts
period_raws
source_urls
missing_fields
```

原始文件在：

```text
s3://cost-raw/<object_key>
```

解析结果建议写到：

```text
s3://cost-extract/info_price/<region_code>/<period>/<sha256>/
```

## 当前随包 manifest 状态

生成时间：2026-07-01

```text
parse_manifest.csv               1845 行，1843 ready
parse_manifest_cost_info.csv     1293 行，1293 ready
```

## 重要边界

- `cost-raw` 是原始文件层，只允许爬虫写入。
- `cost-extract` 是解析加工输出层，同事的解析结果写这里。
- `cost-report` 是 manifest、覆盖矩阵、质检报告层。
- 不要让同事连接 `127.0.0.1:15432`，这个只是某台机器上的本地地址或隧道地址。
- PostgreSQL 和 MinIO 的正式地址必须以 NAS 的真实地址为准。

