#!/usr/bin/env python
"""数据源可达性探针 —— 验证「境外 IP（GitHub Actions）能否采集 A 股数据」

背景
----
本地电脑无法常开，希望用 GitHub Actions 补采那些「当天不跑就永久丢失」的快照型
数据（行业/概念板块、全市场平均股价、涨跌家数、sina 等权涨幅）。但 GH runner 在
境外，国内数据源对美国 IP 是否放行**无法在本地验证** —— 这是整个方案唯一的未知数。

本脚本逐个打这些接口，输出「可达 / 被拦 / 超时 + 耗时」。跑一次即可定论：
  - 全部 OK   → 方案成立，GH Actions 专跑快照型脚本
  - 关键项 FAIL → 改用国内轻量 VPS

用法
----
  python probe_sources.py

在 GitHub Actions 中运行时，若存在 $GITHUB_STEP_SUMMARY，会自动追加 Markdown 表格。

环境变量
--------
  LIXINGER_TOKEN   理杏仁 token（可选；未设置则该项标记 SKIP）
"""
import os
import sys
import time

try:
    import requests
except ImportError:
    print("缺少 requests：pip install -r requirements.txt", file=sys.stderr)
    raise

# 脚本输出含 emoji(✅/❌/⏭)。Windows 控制台默认 GBK 会崩；强制 UTF-8。
# GH runner 已 UTF-8，reconfigure 是幂等的。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

TIMEOUT = 25
# akshare / baostock 内部不走 requests 的 timeout（TCP 直连、内部请求自管），
# 个别源被墙/限流时可能**无限阻塞**，把整个探针拖死。跑在 GH runner 上更危险：
# 一个挂死的源会吃掉 30 分钟 workflow 上限。故给每个探针一个硬看门狗：
# 超时即判 FAIL，并让守护线程继续挂死也不阻塞后续。
PROBE_HARD_TIMEOUT = 90

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

RESULTS = []


class SkipProbe(Exception):
    """环境不具备探测条件（非失败）"""


def _probe_once(fn):
    """在守护线程里跑单个探针，凭容器回传 状态/细节（供看门狗判超时）"""
    box = {}

    def _run():
        try:
            box["detail"] = fn()
            box["status"] = "OK"
        except SkipProbe as e:
            box["status"], box["detail"] = "SKIP", str(e)
        except Exception as e:
            box["status"] = "FAIL"
            box["detail"] = f"{type(e).__name__}: {str(e)[:150]}"

    import threading
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(PROBE_HARD_TIMEOUT)
    if th.is_alive():
        return "FAIL", f"看门狗中断：> {PROBE_HARD_TIMEOUT}s（该源无故阻塞）"
    return box.get("status", "FAIL"), box.get("detail", "")


def record(name, category, fn):
    """执行单个探测（带硬超时看门狗），记录 状态/耗时/细节"""
    t0 = time.time()
    status, detail = _probe_once(fn)
    dt = time.time() - t0
    RESULTS.append({"name": name, "category": category, "status": status,
                    "seconds": round(dt, 2), "detail": detail})
    mark = {"OK": "✅", "FAIL": "❌", "SKIP": "⏭"}[status]
    print(f"  {mark} [{dt:6.2f}s] {name}")
    print(f"           {detail}")
    return status


# ── 原始 HTTP 接口（按生产代码原样打）────────────────────────────────

def _csindex(headers):
    end = time.strftime("%Y%m%d")
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
           f"?indexCode=000300&startDate=20250101&endDate={end}")
    r = requests.get(url, timeout=TIMEOUT, headers=headers)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        raise RuntimeError("HTTP 200 但 data 为空（疑似挑战页/被拦）")
    return f"{len(data)} 条，最新 {data[-1].get('tradeDate')}"


def _eastmoney(host):
    r = requests.get(
        f"https://{host}/api/qt/clist/get",
        params={"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": "m:90+t:3+f:!50",
                "fields": "f3,f6,f12,f14,f104,f105"},
        headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=TIMEOUT)
    r.raise_for_status()
    diff = (r.json().get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError("返回 diff 为空")
    return f"{len(diff)} 个概念板块"


def probe_ths_hot():
    d = time.strftime("%Y-%m-%d")
    url = (f"http://zx.10jqka.com.cn/event/api/getharden/date/{d}"
           "/orderby/date/orderway/desc/charset/GBK/")
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    body = r.content
    if len(body) < 100:
        raise RuntimeError(f"返回体过短（{len(body)} 字节），疑似被拦")
    return f"HTTP {r.status_code}，{len(body)} 字节"


def probe_tencent():
    r = requests.get("https://qt.gtimg.cn/q=sh000001", timeout=TIMEOUT,
                     headers={"User-Agent": UA})
    r.raise_for_status()
    if "v_sh000001" not in r.text:
        raise RuntimeError("返回不含行情字段")
    return f"{len(r.text)} 字节"


def probe_lixinger():
    token = os.environ.get("LIXINGER_TOKEN")
    if not token:
        raise SkipProbe("未设置 LIXINGER_TOKEN")
    r = requests.post(
        "https://open.lixinger.com/api/cn/index/fundamental",
        json={"token": token, "date": time.strftime("%Y-%m-%d"),
              "metricsList": ["pe_ttm"]},
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        timeout=60)
    r.raise_for_status()
    payload = r.json()          # 能解析出 JSON 即说明网络可达
    if payload.get("code") != 1:
        # 可达但业务错误 —— 区分开，避免把「参数不对」误报成「被墙」
        return f"网络可达，但返回 code={payload.get('code')}（{str(payload.get('message'))[:80]}）"
    return "token 有效，接口正常"


# ── akshare 封装接口（延迟 import，避免 akshare 安装失败拖垮整个探针）──

def _ak():
    import akshare as ak
    return ak


def probe_ak_ths_industry():
    df = _ak().stock_board_industry_summary_ths()
    if df is None or df.empty:
        raise RuntimeError("返回空表")
    return f"{len(df)} 个行业板块"


def probe_ak_sina_spot():
    df = _ak().stock_zh_a_spot()
    if df is None or df.empty:
        raise RuntimeError("返回空表")
    return f"{len(df)} 只个股"


def probe_ak_index_daily():
    df = _ak().stock_zh_index_daily(symbol="sh000300")
    if df is None or df.empty:
        raise RuntimeError("返回空表")
    return f"{len(df)} 行，最新 {str(df['date'].iloc[-1])[:10]}"


def probe_ak_zt_pool():
    df = _ak().stock_zt_pool_em(date=time.strftime("%Y%m%d"))
    if df is None or df.empty:
        raise RuntimeError("返回空表")
    return f"{len(df)} 只涨停"


def probe_ak_legulegu():
    df = _ak().stock_market_pe_lg(symbol="创业板")
    if df is None or df.empty:
        raise RuntimeError("返回空表")
    return f"{len(df)} 行"


def probe_baostock():
    import baostock as bs
    lg = bs.login()
    try:
        if lg.error_code != "0":
            raise RuntimeError(f"login 失败: {lg.error_msg}")
        rs = bs.query_trade_dates(start_date="2026-01-01", end_date="2026-12-31")
        n = 0
        while rs.error_code == "0" and rs.next():
            n += 1
        if n == 0:
            raise RuntimeError("login 成功但交易日历返回 0 行")
        return f"login OK，交易日历 {n} 行"
    finally:
        bs.logout()


# ── 探测清单 ─────────────────────────────────────────────────────────
# (显示名, 分类, 是否「快照型数据的关键路径」)
PROBES = [
    ("中证官网 perf API（裸请求，同生产代码）", "PE/指数", True,
     lambda: _csindex(None)),
    ("中证官网 perf API（带浏览器 UA）", "PE/指数", True,
     lambda: _csindex({"User-Agent": UA})),
    ("东财 push2 概念板块", "板块", True,
     lambda: _eastmoney("push2.eastmoney.com")),
    ("东财 push2delay 概念板块", "板块", True,
     lambda: _eastmoney("push2delay.eastmoney.com")),
    ("同花顺 行业板块汇总（akshare）", "板块", True, probe_ak_ths_industry),
    ("sina 全A快照（akshare）", "平均股价/等权", True, probe_ak_sina_spot),
    ("同花顺 强势股 getharden", "题材", False, probe_ths_hot),
    ("腾讯 行情快照", "题材", False, probe_tencent),
    ("东财 涨停池（akshare）", "赚钱效应", False, probe_ak_zt_pool),
    ("akshare 指数日线（沪深300）", "指数日线", False, probe_ak_index_daily),
    ("legulegu 创业板 PE（akshare）", "PE/指数", False, probe_ak_legulegu),
    ("baostock TCP login", "指数/日历", False, probe_baostock),
    ("理杏仁 open API", "PE/指数", False, probe_lixinger),
]


def main():
    print("=" * 72)
    print("  数据源可达性探针")
    print(f"  运行环境: {sys.platform} / TZ={os.environ.get('TZ', '(未设置)')} "
          f"/ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    for name, category, critical, fn in PROBES:
        record(name, category, fn)

    ok = [r for r in RESULTS if r["status"] == "OK"]
    fail = [r for r in RESULTS if r["status"] == "FAIL"]
    skip = [r for r in RESULTS if r["status"] == "SKIP"]
    crit_fail = [r for r in fail if any(p[2] for p in PROBES if p[0] == r["name"])]

    print()
    print("=" * 72)
    print(f"  汇总：可达 {len(ok)} / 失败 {len(fail)} / 跳过 {len(skip)}")
    if crit_fail:
        print(f"  ⚠️ 关键路径失败 {len(crit_fail)} 项 —— 云端方案需要调整：")
        for r in crit_fail:
            print(f"     ❌ {r['name']}")
    elif not fail:
        print("  ✅ 全部可达 —— 快照型数据可以交给 GitHub Actions")
    print("=" * 72)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## 数据源可达性探针\n\n")
            f.write(f"环境：`{sys.platform}` / TZ=`{os.environ.get('TZ', '(未设置)')}`\n\n")
            f.write("| 状态 | 数据源 | 分类 | 耗时(s) | 细节 |\n")
            f.write("|---|---|---|---|---|\n")
            icon = {"OK": "✅", "FAIL": "❌", "SKIP": "⏭"}
            for r in RESULTS:
                f.write(f"| {icon[r['status']]} | {r['name']} | {r['category']} | "
                        f"{r['seconds']} | {r['detail']} |\n")
            f.write(f"\n**汇总**：可达 {len(ok)} / 失败 {len(fail)} / 跳过 {len(skip)}\n")

    # 有关键项失败 → 非零退出，让 CI 显红
    return 1 if crit_fail else 0


if __name__ == "__main__":
    sys.exit(main())
