# Fund-NAV-Update

阿里云企业邮箱 → 自动解析基金净值附件 → 入库 → 一键导出"净值跟踪表"的 Python 系统。

跟踪 21 支私募基金的 **单位净值** 和 **累计净值**,支持横向/纵向/KV 公告等多种 Excel 模板。

---

## 我是哪一种用户?

### 🟢 我只想拿到最新净值表(Windows,不写代码)

1. 打开 **[Actions](https://github.com/zetao011001-create/Fund-NAV-Update/actions)** 标签
2. 找最近一次 *Build Windows EXE* 跑通(绿勾)的 run,点进去
3. 拉到底,在 **Artifacts** 里下载 `nav-updater-windows`
4. 解压得到 `净值跟踪更新.exe`,放桌面
5. **双击运行** → 几秒后桌面出现 `净值跟踪表.xlsx`

之后想更新,再双击一次。

> 如果窗口闪退,桌面会生成 `净值跟踪更新-错误日志.txt`,把它内容发给维护者排查。

### 🟡 我要改代码 / 加功能 / 在 Mac 上跑

完整开发文档:[`fund_nav_system/README.md`](fund_nav_system/README.md)

最小启动:
```bash
cd fund_nav_system
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入邮箱密码
python main.py init-db
python main.py run-once
python main.py export --output ~/Desktop/净值跟踪表.xlsx
```

---

## 仓库结构

```
Fund-NAV-Update/
├── .github/workflows/build-windows.yml   # CI: 自动构建 Windows exe
├── fund_nav_system/                      # 主项目代码
│   ├── src/                              # 核心模块
│   ├── scripts/                          # 一次性脚本
│   ├── dist_seed/fund_nav.db             # 打包用的种子数据库
│   ├── app_entry.py                      # exe 入口
│   ├── nav_updater.spec                  # PyInstaller 配置
│   ├── main.py                           # CLI 入口
│   └── README.md                         # 详细文档
└── README.md                             # (本文件)
```

## 维护备忘

- 凭证存储:GitHub repo **Settings → Secrets and variables → Actions → `EMAIL_PASSWORD`**
- 想用新历史数据出 exe:
  ```bash
  cp fund_nav_system/data/fund_nav.db fund_nav_system/dist_seed/fund_nav.db
  git add -A && git commit -m "Refresh seed DB" && git push
  ```
- 邮箱密码变更:更新上面那个 secret,然后到 Actions 手动 *Run workflow*
