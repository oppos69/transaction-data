import requests
import pandas as pd
import sys
import urllib.request
from datetime import date


def _tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯财经批量行情，补充涨幅/换手/成交额等"""
    prefixed = []
    for c in codes:
        if c.startswith(("6", "9")):
            prefixed.append(f"sh{c}")
        elif c.startswith("8"):
            prefixed.append(f"bj{c}")
        else:
            prefixed.append(f"sz{c}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode("gbk")

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[code] = {
            "收盘价":  float(vals[3]) if vals[3] else 0,
            "涨跌额":  float(vals[31]) if vals[31] else 0,
            "涨幅%":   float(vals[32]) if vals[32] else 0,
            "换手率%": float(vals[38]) if vals[38] else 0,
            "成交额":  float(vals[37]) if vals[37] else 0,
            "成交量":  float(vals[6]) if vals[6] else 0,
        }
    return result


def ths_hot_reason(date_str: str = None) -> pd.DataFrame:
    """同花顺当日强势股题材归因，通过腾讯财经补充行情数据"""
    if date_str is None:
        date_str = date.today().strftime("%Y-%m-%d")

    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{date_str}/orderby/date/orderway/desc/charset/GBK/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=10)
    data = r.json()
    if data.get("errocode", 0) != 0:
        print(f"Error: {data.get('errormsg', '')}")
        return pd.DataFrame()

    rows = data.get("data") or []
    if not rows:
        print(f"{date_str}: No data (non-trading day or not yet updated)")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    rename_map = {
        "name": "名称", "code": "代码", "reason": "题材归因",
        "market": "市场",
    }
    df = df.rename(columns=rename_map)
    df["代码"] = df["代码"].astype(str).str.zfill(6)

    # 通过腾讯财经补充行情数据（收盘价/涨幅/换手率/成交额/成交量）
    try:
        quotes = _tencent_quote(df["代码"].tolist())
        for col in ["收盘价", "涨跌额", "涨幅%", "换手率%", "成交额", "成交量"]:
            df[col] = df["代码"].map(lambda c, _col=col: quotes.get(c, {}).get(_col, 0))
    except Exception as e:
        print(f"Warning: tencent quote failed: {e}")

    # 列顺序
    out_cols = ["id", "名称", "代码", "题材归因", "date", "收盘价", "涨跌额", "涨幅%", "换手率%", "成交额", "成交量", "市场"]
    df = df[[c for c in out_cols if c in df.columns]]

    df["_is_st"] = df["名称"].str.contains(r"\*?ST", case=False, na=False).astype(int)
    df = df.sort_values(["_is_st", "名称"]).drop(columns=["_is_st"]).reset_index(drop=True)
    return df

if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else None

    # 非交易日守卫：同花顺返回「最近快照」且不带日期，非交易日运行会产出幻影行
    from trade_calendar import guard
    if not guard(date_str or date.today().strftime("%Y-%m-%d"), "fetch_ths_hot_reason.py"):
        sys.exit(0)

    df = ths_hot_reason(date_str)
    if df.empty:
        sys.exit(1)

    save_date = date_str or date.today().strftime("%Y-%m-%d")
    filename = f"每日分析/{save_date.replace('-', '')}_强势股题材归因.csv"
    df.to_csv(filename, index=False, encoding="utf-8-sig")

    st_count = df[df["名称"].str.contains("ST", case=False, na=False)].shape[0]
    print(f"Saved: {filename} ({len(df)} stocks, {st_count} ST)")
