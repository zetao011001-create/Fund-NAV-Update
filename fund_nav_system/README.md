# 基金净值自动化采集系统

纯 Python 实现，监控阿里云企业邮箱 → 解析净值附件/正文 → 匹配跟踪列表 → 入库 → 提供查询接口。

## 功能特性

- **邮箱监控**：IMAP 轮询（默认 30 分钟）或 IDLE 长连接（备用）。
- **数据解析**：支持 `.xlsx / .xls / .csv` 附件，以及邮件 HTML / 纯文本正文。
- **核心格式**：横向表格（第一列日期，其余列为基金名）；同时兼容纵向格式。
- **基金匹配**：精确 + 模糊（去除"私募证券投资基金/A 类"等后缀）+ 包含匹配；非跟踪基金不入库。
- **幂等入库**：`(fund_id, nav_date)` 联合唯一键；重复邮件不会重复写。
- **查询接口**：纯 Python 函数返回 pandas DataFrame，后期可包装为 FastAPI。
- **CLI**：`init-db / run-once / daemon / query / export / status / retry-failed` 等。

## 项目结构

```
fund_nav_system/
├── .env                     # 环境变量（含密码，勿提交）
├── .env.example
├── requirements.txt
├── README.md
├── main.py                  # CLI 入口
├── init_db.py               # 初始化 + 导入 21 只预设基金
├── src/
│   ├── config.py            # 配置中心
│   ├── logger.py            # loguru 配置
│   ├── database.py          # SQLAlchemy ORM + DatabaseManager
│   ├── email_client.py      # IMAP 客户端
│   ├── parser.py            # 横向 + 纵向表格解析
│   ├── fund_matcher.py      # 基金匹配
│   ├── sync_engine.py       # 主工作流
│   └── query_api.py         # 查询接口
├── data/                    # SQLite + 附件 + 导出
└── logs/                    # 日志
```

## 快速开始

### 1. 准备 Python 环境

要求 Python 3.10+。

```bash
cd fund_nav_system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入邮箱密码等信息
```

`.env` 必填项：

| 变量 | 说明 |
| --- | --- |
| `EMAIL_ADDRESS` | 阿里云企业邮箱地址 |
| `EMAIL_PASSWORD` | 邮箱密码（建议使用客户端授权码） |
| `IMAP_SERVER` | 默认 `imap.qiye.aliyun.com` |
| `IMAP_PORT` | 默认 `993` |
| `DATABASE_URL` | 默认 `sqlite:///data/fund_nav.db` |
| `POLL_INTERVAL` | 轮询间隔（分钟），默认 30 |
| `LOG_LEVEL` | 日志级别 |
| `EMAIL_LOOKBACK_DAYS` | 首次/每次回溯天数，默认 7 |
| `MARK_AS_READ` | 处理成功后是否标记为已读，默认 false |

### 3. 初始化数据库

```bash
python init_db.py
# 或：
python main.py init-db
```

会创建三张表（`funds` / `nav_history` / `email_log`），并把 21 只预设基金导入 `funds`。

### 4. 测试一次拉取

```bash
python main.py run-once --since-days 7
```

输出示例：

```
emails_scanned   : 5
emails_matched   : 3
emails_succeeded : 3
emails_failed    : 0
emails_ignored   : 2
nav_inserted     : 42
nav_updated      : 0
```

### 5. 启动守护进程

```bash
python main.py daemon
# 或自定义间隔：
python main.py daemon --interval 15
```

## CLI 命令参考

| 命令 | 说明 |
| --- | --- |
| `init-db` | 创建表 + 导入预设基金 |
| `run-once [--since-days N] [--only-unseen]` | 单次执行（测试用） |
| `daemon [--interval N]` | 守护进程（默认 30 分钟） |
| `retry-failed` | 重试所有 status=failed 的邮件 |
| `query [--fund X] [--start ...] [--end ...] [--limit N]` | 查询净值 |
| `latest` | 所有基金的最新净值 |
| `export [--start ...] [--end ...] [--output PATH]` | 按分类导出 Excel |
| `list-funds [--category X] [--manager Y]` | 列出基金 |
| `add-fund NAME --manager X --strategy Y --category Z` | 新增/更新基金 |
| `update-fund NAME --category Z ...` | 更新基金字段 |
| `status` | 查看系统状态 |

## 在代码中查询

```python
from src.query_api import query_nav, get_latest_nav_all, get_nav_series, export_to_excel

# 1. 查询某只基金的净值历史
df = query_nav(fund_name="恒如全天候守心2号", start_date="2024-01-01", end_date="2024-12-31")

# 2. 所有基金的最新净值
latest = get_latest_nav_all()

# 3. 拿到一个日期索引的 Series（可直接画图）
s = get_nav_series("恒如守心6号")
s.plot()

# 4. 按分类导出宽表 Excel
path = export_to_excel(start_date="2024-01-01")
```

## 数据库表结构

### `funds`
基金主表，预设跟踪列表 + 用户自加的基金。

### `nav_history`
净值历史。`UNIQUE(fund_id, nav_date)` 保证幂等。

### `email_log`
邮件处理日志，`status` 取值：
- `success`：成功入库
- `failed`：处理失败（有错误）
- `ignored`：非净值主题，已跳过
- `no_data`：识别为净值邮件但解析不出任何数据
- `pending`：处理中

`raw_snapshot` 会存放未匹配到的基金名 + 错误信息，便于人工排查。

## 解析逻辑说明

### 横向表格（核心场景）

```
日期         | 恒如全天候守心2号 | 恒如守心6号 | ...
2025-05-09   |     1.0214        |   1.0387    | ...
2025-05-16   |     1.0231        |   1.0392    | ...
```

- 自动识别第一列为日期（或列名含"日期/Date/时间"）。
- 其余每列视为一个基金名 → 用 `FundMatcher` 匹配到跟踪列表。
- 单元格为数字 → 写入 `nav_history`；非数字/空值 → 跳过。
- 列名是"合计/总计/管理人/规模"等关键词 → 直接丢弃。

### 纵向表格（备用）

```
产品名称           | 净值日期    | 单位净值
恒如全天候守心2号  | 2025-05-09 | 1.0214
```

通过列名别名识别。

### 日期解析

支持 `YYYY-MM-DD / YYYY/MM/DD / YYYY年MM月DD日 / YYYYMMDD / Excel 序列号`。

### 基金名匹配

1. 精确匹配跟踪列表全名。
2. 去除 `私募证券投资基金 / 私募基金 / A 类 / B 类` 等后缀后匹配。
3. 双向包含匹配（跟踪短名包含于邮件中的全名）。
4. 仍不匹配 → 写入 `email_log.raw_snapshot.unmatched_names`，不入库。

## 常见问题

**Q: 阿里云 IMAP 提示"登录失败"？**
A: 检查邮箱后台是否开启 IMAP/SMTP 服务，并确认使用客户端授权码而非网页登录密码。

**Q: IDLE 模式连接很快断开？**
A: 阿里云企业邮对 IDLE 支持不稳定，**推荐使用 daemon 轮询模式**（已是默认）。

**Q: 邮件被标记为"failed"或"no_data"怎么办？**
A: 查 `email_log.raw_snapshot`（JSON）：
- `unmatched_names`：检查是否需要补充模糊匹配规则或加入 `funds` 表。
- `errors`：检查日志了解具体异常。
- 修复后运行 `python main.py retry-failed`。

**Q: 想新增/修改跟踪基金？**
A:
```bash
python main.py add-fund "新基金名称" --manager "某资产" --strategy "套利" --category "套利"
python main.py update-fund "恒如全天候守心2号" --category "套利"
```

**Q: 想停用某只基金？**
A:
```bash
python main.py update-fund "梧桐1号" --no-tracking
```

**Q: 迁移到 PostgreSQL？**
A: 改 `.env` 的 `DATABASE_URL=postgresql://user:pwd@host:5432/db`，重新 `init-db` 即可。

## 安全与运维

- `.env` **必须** 加入 `.gitignore`，禁止提交。
- 日志按天滚动，保留 30 天，位置 `logs/fund_nav_YYYY-MM-DD.log`。
- 数据库定期备份：`cp data/fund_nav.db data/fund_nav.db.bak.$(date +%F)`。
- 守护进程可用 `nohup / systemd / supervisor` 托管。

### systemd 示例

```ini
[Unit]
Description=Fund NAV Daemon
After=network.target

[Service]
WorkingDirectory=/opt/fund_nav_system
ExecStart=/opt/fund_nav_system/.venv/bin/python main.py daemon
Restart=always
User=appuser

[Install]
WantedBy=multi-user.target
```
