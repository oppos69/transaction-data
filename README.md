# A股快照数据采集（GitHub Actions）

本地电脑无法常开，有一类**快照型 A 股数据**「当天不跑就永久丢失」——数据源只返回最近收盘快照、
不带日期、不支持历史回填。本仓库用 GitHub Actions 每天盘后定时补采，把当日 CSV 提交到这里；
本地定期 `git pull` 即可拿回 PC 关机日子缺失的数据，现有分析脚本无需改动。

## 采集范围（5 个脚本，顺序由 `collect_snapshots.py` 固定）

| 脚本 | 产出 |
|---|---|
| `fetch_avg_price_update.py` | `avg_price_daily.csv` 全市场平均/中位股价（sina） |
| `fetch_sector_daily.py` | `每日分析/YYYYMMDD_行业板块.csv`、`概念板块.csv` |
| `fetch_market_daily.py` | `每日分析/YYYYMMDD_市场概况.csv`（涨跌家数/平均股价/成交） |
| `fetch_money_effect.py` | `money_effect.csv`（sina 等权 + 涨停跌停池） |
| `fetch_ths_hot_reason.py` | `每日分析/YYYYMMDD_强势股题材归因.csv` |

交易日由 `trade_calendar.guard()` 判定；非交易日全部跳过、不产生空提交。日历源优先用入库的
`trade_dates.csv`（`_refresh_calendar.py` 每日经 akshare-sina 刷新），baostock 作在线兜底。

## 工作流

- `collect.yml` —— 每天 `07:35 UTC`（=15:35 北京）运行 `collect_snapshots.py`，采集后把当日
  CSV commit + push 回本仓库；支持 `workflow_dispatch` 手动冒烟。
- `probe.yml` —— 手动触发的「数据源可达性探针」，验证境外 runner 能否访问各国内数据源，
  关键项失败即 CI 显红（境外可行性定论用）。

## 本地运行

```bash
pip install -r requirements.txt
python _refresh_calendar.py          # 刷新交易日历（可选）
python collect_snapshots.py          # 采集今天
python collect_snapshots.py 2026-10-08   # 采集指定交易日
python probe_sources.py              # 本地探针
```

依赖到本地分析仓库回流：`git pull` 后，把 `每日分析/`、`avg_price_daily.csv`、`money_effect.csv`
合并回分析仓库对应位置即可。