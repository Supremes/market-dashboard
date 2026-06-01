"""
Market Valuation Dashboard - Data Fetcher
从多个数据源获取纳斯达克100和标普500的估值数据
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ==================== 配置 ====================

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# ETF 代理
PROXIES = {
    "SPY": {"name": "标普500", "name_en": "S&P 500"},
    "QQQ": {"name": "纳斯达克100", "name_en": "NASDAQ 100"},
}

# 缓存过期时间（秒）
CACHE_TTL = {
    "cape": 86400,         # CAPE 每天更新
    "treasury": 86400,     # 国债收益率每天更新
    "buffett": 604800,     # 巴菲特指标每周更新
    "div_hist": 604800,    # 股息历史每周更新
    "price": 3600,         # 价格每小时更新
}

CACHE_DIR = Path(__file__).parent / "data"


class MarketDataFetcher:
    """市场数据获取与缓存管理"""

    def __init__(self):
        CACHE_DIR.mkdir(exist_ok=True)
        self._cache = {}
        self._cache_ts = {}
        self._lock = threading.Lock()
        self._request_count = 0
        self._last_request = 0

    # ==================== 缓存管理 ====================

    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.json"

    def _load_cache(self, key: str) -> dict | None:
        """从文件缓存加载"""
        path = self._cache_path(key)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if time.time() - data.get("ts", 0) < CACHE_TTL.get(key, 3600):
                    return data.get("payload")
            except Exception as e:
                logger.warning(f"缓存读取失败 {key}: {e}")
        return None

    def _save_cache(self, key: str, payload):
        """保存到文件缓存"""
        try:
            path = self._cache_path(key)
            path.write_text(json.dumps({
                "ts": time.time(),
                "payload": payload
            }, default=str))
        except Exception as e:
            logger.warning(f"缓存保存失败 {key}: {e}")

    def _get_cached(self, key: str):
        """优先内存缓存，其次文件缓存"""
        with self._lock:
            if key in self._cache:
                if time.time() - self._cache_ts.get(key, 0) < CACHE_TTL.get(key, 3600):
                    return self._cache[key]

        payload = self._load_cache(key)
        if payload is not None:
            with self._lock:
                self._cache[key] = payload
                self._cache_ts[key] = time.time()
            return payload
        return None

    def _set_cached(self, key: str, payload):
        """同时更新内存和文件缓存"""
        with self._lock:
            self._cache[key] = payload
            self._cache_ts[key] = time.time()
        self._save_cache(key, payload)

    # ==================== HTTP 工具 ====================

    def _rate_limit(self):
        """简单限速：至少间隔0.5秒"""
        elapsed = time.time() - self._last_request
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)
        self._last_request = time.time()

    def _fetch_url(self, url: str, timeout=15) -> str | None:
        """通用 HTTP GET"""
        self._rate_limit()
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            self._request_count += 1
            return resp.text
        except requests.RequestException as e:
            logger.warning(f"请求失败 {url}: {e}")
            return None

    # ==================== 核心数据获取 ====================

    def get_index_data(self, ticker: str) -> dict | None:
        """获取指数当前数据（价格、PE、PB、股息率等）"""
        cache_key = f"index_{ticker}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            t = yf.Ticker(ticker)
            info = t.info or {}

            current_price = info.get("regularMarketPrice") or info.get("previousClose", 0)
            if not current_price:
                hist = t.history(period="5d")
                if not hist.empty:
                    current_price = float(hist["Close"].iloc[-1])

            if not current_price:
                return None

            trailing_pe = info.get("trailingPE")
            forward_pe = info.get("forwardPE")
            pb_ratio = info.get("priceToBook")
            dividend_yield = info.get("dividendYield")
            # yfinance dividendYield 对于 ETF 返回的已经是百分比值(如 1.03 = 1.03%)
            # 不需要额外转换

            fifty_two_week_high = info.get("fiftyTwoWeekHigh", current_price * 1.1)
            fifty_two_week_low = info.get("fiftyTwoWeekLow", current_price * 0.9)

            result = {
                "ticker": ticker,
                "name": PROXIES.get(ticker, {}).get("name", ticker),
                "name_en": PROXIES.get(ticker, {}).get("name_en", ticker),
                "price": round(current_price, 2),
                "trailing_pe": round(trailing_pe, 2) if trailing_pe else None,
                "forward_pe": round(forward_pe, 2) if forward_pe else None,
                "pb_ratio": round(pb_ratio, 2) if pb_ratio else None,
                "dividend_yield": round(dividend_yield, 3) if dividend_yield else None,
                "fifty_two_week_high": round(fifty_two_week_high, 2),
                "fifty_two_week_low": round(fifty_two_week_low, 2),
                "market_cap": info.get("marketCap"),
                "volume": info.get("volume"),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

            self._set_cached(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"获取 {ticker} 数据失败: {e}")
            return None

    def get_cape(self, ticker: str = "SPY") -> dict | None:
        """获取 Shiller CAPE 比率"""
        cache_key = "cape"
        cached = self._get_cached(cache_key)
        if cached:
            if cached.get("ticker") == ticker or ticker in ("SPY", "QQQ"):
                return cached

        # 方法1：尝试从 multpl.com 获取
        cape_val = self._fetch_cape_from_multpl()

        if cape_val:
            result = {
                "ticker": ticker,
                "cape": cape_val,
                "source": "multpl.com",
                "fetched_at": datetime.now().isoformat(),
            }
            self._set_cached(cache_key, result)
            return result

        # 方法2：用 yfinance 的 trailingPE 作为近似
        index_data = self.get_index_data(ticker)
        if index_data and index_data.get("trailing_pe"):
            pe = index_data["trailing_pe"]
            cape_approx = round(pe * 1.15, 2)  # CAPE 通常比 trailing PE 高10-20%
            result = {
                "ticker": ticker,
                "cape": cape_approx,
                "source": "estimated_from_pe",
                "fetched_at": datetime.now().isoformat(),
            }
            self._set_cached(cache_key, result)
            return result

        return None

    def _fetch_cape_from_multpl(self) -> float | None:
        """从 multpl.com 获取当前 CAPE"""
        try:
            html = self._fetch_url("https://www.multpl.com/shiller-pe", timeout=10)
            if not html:
                return None

            import re
            # 查找当前值 - 格式: "Current Shiller PE Ratio is 37.54"
            patterns = [
                r'Current\s+Shiller\s+PE\s+Ratio\s+is\s+([\d.]+)',
                r'id="current"[^>]*>([\d.]+)',
                r'Shiller PE Ratio.*?is\s+([\d.]+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    return float(match.group(1))
            return None

        except Exception as e:
            logger.warning(f"获取 CAPE 失败: {e}")
            return None

    def get_treasury_yield(self) -> dict:
        """获取10年期国债收益率"""
        cache_key = "treasury"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        yield_val = None
        source = None

        # 方法1：FRED API
        if FRED_API_KEY:
            try:
                url = (f"{FRED_BASE}?series_id=DGS10&api_key={FRED_API_KEY}"
                       f"&file_type=json&sort_order=desc&limit=5")
                text = self._fetch_url(url)
                if text:
                    data = json.loads(text)
                    for obs in data.get("observations", []):
                        if obs.get("value") and obs["value"] != ".":
                            yield_val = float(obs["value"])
                            source = "fred"
                            break
            except Exception as e:
                logger.warning(f"FRED 获取国债收益率失败: {e}")

        # 方法2：从 multpl.com 获取
        if yield_val is None:
            try:
                html = self._fetch_url("https://www.multpl.com/10-year-treasury-rate", timeout=10)
                if html:
                    import re
                    patterns = [
                        r'Current\s+10[- ]Year\s+Treasury\s+Rate\s+is\s+([\d.]+)',
                        r'id="current"[^>]*>([\d.]+)',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, html, re.IGNORECASE)
                        if match:
                            yield_val = float(match.group(1))
                            source = "multpl.com"
                            break
            except Exception as e:
                logger.warning(f"multpl.com 获取国债收益率失败: {e}")

        # 方法3：用 yfinance
        if yield_val is None:
            try:
                t = yf.Ticker("^TNX")
                hist = t.history(period="5d")
                if not hist.empty:
                    yield_val = round(float(hist["Close"].iloc[-1]), 3)
                    source = "yfinance"
            except Exception as e:
                logger.warning(f"yfinance 获取国债收益率失败: {e}")

        # 最终回退
        if yield_val is None:
            yield_val = 4.25
            source = "fallback"

        result = {
            "yield_10y": yield_val,
            "source": source,
            "fetched_at": datetime.now().isoformat(),
        }
        self._set_cached(cache_key, result)
        return result

    def get_buffett_indicator(self) -> dict | None:
        """获取巴菲特指标（Wilshire 5000 / GDP）"""
        if not FRED_API_KEY:
            return None

        cache_key = "buffett"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            # 获取 GDP
            url = (f"{FRED_BASE}?series_id=GDP&api_key={FRED_API_KEY}"
                   f"&file_type=json&sort_order=desc&limit=5")
            text = self._fetch_url(url)
            if not text:
                return None

            gdp_data = json.loads(text)
            gdp = None
            for obs in gdp_data.get("observations", []):
                if obs.get("value") and obs["value"] != ".":
                    gdp = float(obs["value"])
                    break

            if not gdp:
                return None

            # 获取 Wilshire 5000
            url2 = (f"{FRED_BASE}?series_id=WILL5000IND&api_key={FRED_API_KEY}"
                    f"&file_type=json&sort_order=desc&limit=5")
            text2 = self._fetch_url(url2)
            if not text2:
                return None

            wilshire_data = json.loads(text2)
            wilshire = None
            for obs in wilshire_data.get("observations", []):
                if obs.get("value") and obs["value"] != ".":
                    wilshire = float(obs["value"])
                    break

            if not wilshire:
                return None

            # 巴菲特指标 = Wilshire 5000 / GDP * 100
            ratio = round((wilshire / gdp) * 100, 1)

            # 历史百分位估算
            # 历史范围：约50%（低估）到 200%+（极度高估）
            # 中位数约100-120%
            if ratio <= 80:
                percentile = 10
            elif ratio <= 100:
                percentile = 25
            elif ratio <= 120:
                percentile = 50
            elif ratio <= 150:
                percentile = 70
            elif ratio <= 180:
                percentile = 85
            else:
                percentile = min(99, 85 + (ratio - 180) / 10)

            result = {
                "ratio": ratio,
                "gdp_billion": round(gdp, 1),
                "wilshire": round(wilshire, 2),
                "percentile": round(percentile, 1),
                "source": "fred",
                "fetched_at": datetime.now().isoformat(),
            }
            self._set_cached(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"获取巴菲特指标失败: {e}")
            return None

    def get_historical_cape(self) -> list[dict] | None:
        """获取 CAPE 历史数据（用于图表）"""
        cache_key = "cape_hist"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            html = self._fetch_url("https://www.multpl.com/shiller-pe/table/by-month")
            if not html:
                return None

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table tr")

            history = []
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    date_str = cols[0].get_text(strip=True)
                    val_str = cols[1].get_text(strip=True).replace(",", "")
                    try:
                        date = datetime.strptime(date_str, "%Jan %d, %Y")
                    except ValueError:
                        try:
                            date = datetime.strptime(date_str, "%b %d, %Y")
                        except ValueError:
                            continue
                    try:
                        val = float(val_str)
                    except ValueError:
                        continue
                    history.append({
                        "date": date.strftime("%Y-%m"),
                        "value": val
                    })

            if history:
                # 只保留最近20年
                cutoff = (datetime.now() - timedelta(days=365*20)).strftime("%Y-%m")
                history = [h for h in history if h["date"] >= cutoff]
                history.reverse()
                self._set_cached(cache_key, history)
                return history

        except Exception as e:
            logger.warning(f"获取 CAPE 历史失败: {e}")

        return None

    def get_historical_dividend_yield(self) -> list[dict] | None:
        """获取股息率历史数据"""
        cache_key = "div_hist"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            html = self._fetch_url("https://www.multpl.com/s-p-500-dividend-yield/table/by-month")
            if not html:
                return None

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table tr")

            history = []
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    date_str = cols[0].get_text(strip=True)
                    val_str = cols[1].get_text(strip=True).replace(",", "").replace("%", "")
                    try:
                        date = datetime.strptime(date_str, "%b %d, %Y")
                    except ValueError:
                        try:
                            date = datetime.strptime(date_str, "%Jan %d, %Y")
                        except ValueError:
                            continue
                    try:
                        val = float(val_str)
                    except ValueError:
                        continue
                    history.append({
                        "date": date.strftime("%Y-%m"),
                        "value": val
                    })

            if history:
                cutoff = (datetime.now() - timedelta(days=365*20)).strftime("%Y-%m")
                history = [h for h in history if h["date"] >= cutoff]
                history.reverse()
                self._set_cached(cache_key, history)
                return history

        except Exception as e:
            logger.warning(f"获取股息率历史失败: {e}")

        return None

    def _fetch_multpl_history(self, url: str, is_percent: bool = False) -> list[dict] | None:
        """从 multpl.com 抓取历史表格数据（通用方法）"""
        try:
            html = self._fetch_url(url, timeout=15)
            if not html:
                return None
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table tr")
            history = []
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                date_str = cols[0].get_text(strip=True)
                val_str = cols[1].get_text(strip=True)
                # 去掉 dagger(†)、逗号、百分号
                import re
                val_str = re.sub(r'[†%,]', '', val_str).strip()
                for fmt in ["%b %d, %Y", "%B %d, %Y"]:
                    try:
                        date = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue
                try:
                    val = float(val_str)
                except ValueError:
                    continue
                history.append({"date": date.strftime("%Y-%m"), "value": val})
            return history if history else None
        except Exception as e:
            logger.warning(f"抓取 multpl 历史数据失败 {url}: {e}")
            return None

    def get_historical_pe(self) -> list[dict] | None:
        """获取 S&P 500 PE 历史数据"""
        cache_key = "pe_hist"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        data = self._fetch_multpl_history(
            "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
        )
        if data:
            cutoff = (datetime.now() - timedelta(days=365 * 20)).strftime("%Y-%m")
            data = [d for d in data if d["date"] >= cutoff]
            data.reverse()
            self._set_cached(cache_key, data)
        return data

    @staticmethod
    def compute_10y_stats(current_value: float, history: list[dict]) -> dict | None:
        """计算当前值在近 10 年历史中的统计: percentile, min, max, p20, p80"""
        if not history or current_value is None:
            return None
        cutoff = (datetime.now() - timedelta(days=365 * 10)).strftime("%Y-%m")
        vals = sorted([d["value"] for d in history if d["date"] >= cutoff and d["value"] is not None])
        if len(vals) < 5:
            return None
        import statistics
        count_below = sum(1 for v in vals if v < current_value)
        return {
            "percentile": round(count_below / len(vals) * 100),
            "min": round(vals[0], 2),
            "max": round(vals[-1], 2),
            "p20": round(vals[int(len(vals) * 0.2)], 2),
            "p80": round(vals[int(len(vals) * 0.8)], 2),
        }

    def get_us_percentiles(self) -> dict:
        """获取美股各指标的近十年百分位统计"""
        result = {}
        # PE 百分位
        pe_hist = self.get_historical_pe()
        if pe_hist:
            current_pe = pe_hist[-1]["value"] if pe_hist else None
            if current_pe:
                result["pe"] = self.compute_10y_stats(current_pe, pe_hist) or {}
                result["pe"]["current"] = current_pe
        # CAPE 百分位
        cape_hist = self.get_historical_cape()
        if cape_hist:
            current_cape = cape_hist[-1]["value"] if cape_hist else None
            if current_cape:
                result["cape"] = self.compute_10y_stats(current_cape, cape_hist) or {}
                result["cape"]["current"] = current_cape
        # PB 百分位
        pb_hist = self._fetch_multpl_history(
            "https://www.multpl.com/s-p-500-price-to-book/table/by-month"
        )
        if pb_hist:
            current_pb = pb_hist[-1]["value"] if pb_hist else None
            if current_pb:
                result["pb"] = self.compute_10y_stats(current_pb, pb_hist) or {}
                result["pb"]["current"] = current_pb
        # 股息率百分位
        div_hist = self.get_historical_dividend_yield()
        if div_hist:
            current_div = div_hist[-1]["value"] if div_hist else None
            if current_div:
                result["dividend"] = self.compute_10y_stats(current_div, div_hist) or {}
                result["dividend"]["current"] = current_div
        # MA200 偏离百分位（从价格历史计算）
        for ticker in ["SPY", "QQQ"]:
            ma_stats = self._compute_ma_deviation_stats(ticker)
            if ma_stats is not None:
                result[f"ma200_{ticker}"] = ma_stats
        return result

    def _compute_ma_deviation_stats(self, ticker: str) -> dict | None:
        """从 10 年价格历史计算 MA200 偏离的完整统计"""
        try:
            hist = self.get_price_history(ticker, "10y")
            if not hist or len(hist) < 250:
                return None
            closes = [d["close"] for d in hist]
            devs = []
            for i in range(200, len(closes)):
                ma200 = sum(closes[i - 200:i]) / 200
                dev = (closes[i] - ma200) / ma200 * 100
                devs.append(dev)
            if not devs or len(devs) < 10:
                return None
            devs_sorted = sorted(devs)
            current_dev = devs[-1]
            count_below = sum(1 for d in devs if d < current_dev)
            return {
                "percentile": round(count_below / len(devs) * 100),
                "min": round(devs_sorted[0], 2),
                "max": round(devs_sorted[-1], 2),
                "p20": round(devs_sorted[int(len(devs_sorted) * 0.2)], 2),
                "p80": round(devs_sorted[int(len(devs_sorted) * 0.8)], 2),
                "current": round(current_dev, 2),
            }
        except Exception as e:
            logger.warning(f"计算 {ticker} MA200 偏离统计失败: {e}")
            return None

    def get_hk_percentiles(self) -> dict:
        """获取港股各指标的近十年百分位统计"""
        result = {}
        for label, ticker in [("hsi", "^HSI"), ("etf_3110", "3110.HK")]:
            ma_stats = self._compute_ma_deviation_stats(ticker)
            if ma_stats is not None:
                result[f"ma200_{label}"] = ma_stats
        return result

    def get_price_history(self, ticker: str, period: str = "10y") -> list[dict] | None:
        """获取价格历史数据"""
        cache_key = f"price_hist_{ticker}_{period}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            t = yf.Ticker(ticker)
            hist = t.history(period=period)
            if hist.empty:
                return None

            history = []
            for date, row in hist.iterrows():
                close = row.get("Close")
                # 跳过 NaN 值（港股常见）
                if close is None or str(close) == "nan":
                    continue
                history.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "close": round(float(close), 2),
                    "volume": int(row.get("Volume", 0) or 0),
                })

            if history:
                self._set_cached(cache_key, history)
                return history

        except Exception as e:
            logger.warning(f"获取 {ticker} 价格历史失败: {e}")

        return None

    def get_ma_deviation(self, ticker: str) -> dict | None:
        """计算价格偏离200日均线的百分比"""
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1y")
            if hist.empty or len(hist) < 200:
                hist = t.history(period="2y")
                if hist.empty or len(hist) < 200:
                    return None

            # 港股可能返回 NaN，需要 dropna
            closes = hist["Close"].dropna()
            if len(closes) < 200:
                return None

            current_price = float(closes.iloc[-1])
            ma200 = float(closes.iloc[-200:].mean())
            deviation = round(((current_price - ma200) / ma200) * 100, 2)

            return {
                "ticker": ticker,
                "current_price": round(current_price, 2),
                "ma200": round(ma200, 2),
                "deviation": deviation,
                "fetched_at": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.warning(f"计算 {ticker} MA200 偏离失败: {e}")
            return None

    # ==================== 估值评分 ====================

    @staticmethod
    def score_cape(cape: float) -> tuple[int, str, str]:
        """CAPE 评分：历史百分位估算"""
        if cape <= 15:
            return 90, "极度便宜", "low"
        elif cape <= 20:
            return 75, "偏便宜", "low"
        elif cape <= 25:
            return 60, "合理偏低", "medium"
        elif cape <= 30:
            return 45, "合理", "medium"
        elif cape <= 35:
            return 30, "合理偏高", "high"
        elif cape <= 40:
            return 15, "偏贵", "high"
        else:
            return 5, "极度偏贵", "high"

    @staticmethod
    def score_pe(pe: float) -> tuple[int, str, str]:
        """PE 评分"""
        if pe is None:
            return 50, "数据不足", "medium"
        if pe <= 12:
            return 90, "极度便宜", "low"
        elif pe <= 16:
            return 75, "偏便宜", "low"
        elif pe <= 20:
            return 60, "合理偏低", "medium"
        elif pe <= 25:
            return 45, "合理", "medium"
        elif pe <= 30:
            return 30, "合理偏高", "high"
        elif pe <= 35:
            return 15, "偏贵", "high"
        else:
            return 5, "极度偏贵", "high"

    @staticmethod
    def score_pb(pb: float) -> tuple[int, str, str]:
        """PB 评分"""
        if pb is None:
            return 50, "数据不足", "medium"
        if pb <= 2.0:
            return 85, "偏便宜", "low"
        elif pb <= 3.0:
            return 65, "合理偏低", "medium"
        elif pb <= 4.0:
            return 45, "合理", "medium"
        elif pb <= 5.0:
            return 25, "偏贵", "high"
        else:
            return 10, "极度偏贵", "high"

    @staticmethod
    def score_dividend_yield(dy: float) -> tuple[int, str, str]:
        """股息率评分（高股息 = 便宜）"""
        if dy is None:
            return 50, "数据不足", "medium"
        if dy >= 3.0:
            return 90, "极度便宜", "low"
        elif dy >= 2.0:
            return 70, "偏便宜", "low"
        elif dy >= 1.5:
            return 50, "合理", "medium"
        elif dy >= 1.2:
            return 35, "合理偏高", "medium"
        elif dy >= 1.0:
            return 20, "偏贵", "high"
        else:
            return 5, "极度偏贵", "high"

    @staticmethod
    def score_ma_deviation(deviation: float) -> tuple[int, str, str]:
        """MA200 偏离评分"""
        if deviation <= -20:
            return 95, "极度超卖", "low"
        elif deviation <= -10:
            return 80, "超卖", "low"
        elif deviation <= -5:
            return 65, "偏低于均线", "medium"
        elif deviation <= 5:
            return 50, "接近均线", "medium"
        elif deviation <= 10:
            return 35, "偏高于均线", "medium"
        elif deviation <= 20:
            return 20, "超买", "high"
        else:
            return 5, "极度超买", "high"

    @staticmethod
    def score_risk_premium(rp: float) -> tuple[int, str, str]:
        """风险溢价评分（高溢价 = 股票相对便宜）"""
        if rp >= 5:
            return 90, "极具吸引力", "low"
        elif rp >= 3:
            return 70, "偏便宜", "low"
        elif rp >= 1:
            return 50, "合理", "medium"
        elif rp >= -1:
            return 30, "合理偏高", "high"
        else:
            return 10, "股票偏贵", "high"

    @staticmethod
    def score_buffett(ratio: float) -> tuple[int, str, str]:
        """巴菲特指标评分"""
        if ratio <= 80:
            return 85, "偏便宜", "low"
        elif ratio <= 100:
            return 65, "合理偏低", "medium"
        elif ratio <= 130:
            return 45, "合理", "medium"
        elif ratio <= 170:
            return 25, "偏贵", "high"
        else:
            return 5, "极度偏贵", "high"

    @staticmethod
    def overall_signal(score: float) -> tuple[str, str, str]:
        """综合评分 -> 信号"""
        if score >= 70:
            return "🟢 偏便宜", "当前市场估值偏低，可能是入场好时机", "bullish"
        elif score >= 55:
            return "🟢 合理偏低", "市场估值处于合理区间偏低水平", "neutral_bullish"
        elif score >= 45:
            return "🟡 合理", "市场估值处于合理区间", "neutral"
        elif score >= 35:
            return "🟠 合理偏高", "市场估值略高于合理水平，需谨慎", "neutral_bearish"
        elif score >= 20:
            return "🔴 偏贵", "市场估值偏高，建议控制仓位", "bearish"
        else:
            return "🔴🔴 极度偏贵", "市场估值极高，风险较大", "very_bearish"

    # ==================== 主数据聚合 ====================

    def get_dashboard_data(self) -> dict:
        """获取仪表盘所需的全部数据"""
        result = {
            "indices": [],
            "buffett": None,
            "overall": None,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        all_scores = []

        # 获取近十年百分位数据
        pctl = self.get_us_percentiles()

        for ticker in ["SPY", "QQQ"]:
            index_data = self.get_index_data(ticker)
            if not index_data:
                continue

            # 获取各维度数据
            cape_data = self.get_cape(ticker)
            treasury = self.get_treasury_yield()
            ma_data = self.get_ma_deviation(ticker)

            # 计算各维度评分
            metrics = []

            # 1. PE 评分
            pe = index_data.get("trailing_pe")
            pe_score, pe_signal, pe_level = self.score_pe(pe)
            pe_pctl = pctl.get("pe", {}).get("percentile")
            metrics.append({
                "name": "市盈率 (PE)",
                "name_en": "P/E Ratio",
                "value": f"{pe:.1f}" if pe else "N/A",
                "score": pe_score,
                "signal": pe_signal,
                "level": pe_level,
                "description": "股票价格与每股收益的比值",
                "benchmark": "历史中位数约 15-20",
                "stats_10y": pctl.get("pe"),
                "source_url": "https://www.multpl.com/s-p-500-pe-ratio",
            })

            # 2. CAPE 评分
            cape = cape_data.get("cape") if cape_data else None
            if cape:
                cape_score, cape_signal, cape_level = self.score_cape(cape)
                cape_source = "Shiller CAPE" if cape_data.get("source") == "multpl.com" else "近似估算"
                metrics.append({
                    "name": "周期调整市盈率 (CAPE)",
                    "name_en": "Shiller CAPE",
                    "value": f"{cape:.1f}",
                    "score": cape_score,
                    "signal": cape_signal,
                    "level": cape_level,
                    "description": "10年周期调整后的市盈率，消除短期波动",
                    "benchmark": "历史中位数约 16-17",
                    "source": cape_source,
                    "stats_10y": pctl.get("cape"),
                    "source_url": "https://www.multpl.com/shiller-pe",
                })

            # 3. PB 评分
            pb = index_data.get("pb_ratio")
            pb_score, pb_signal, pb_level = self.score_pb(pb)
            metrics.append({
                "name": "市净率 (PB)",
                "name_en": "P/B Ratio",
                "value": f"{pb:.2f}" if pb else "N/A",
                "score": pb_score,
                "signal": pb_signal,
                "level": pb_level,
                "description": "股票价格与每股净资产的比值",
                "benchmark": "历史中位数约 2.5-3.5",
                "stats_10y": pctl.get("pb"),
                "source_url": "https://www.multpl.com/s-p-500-price-to-book",
            })

            # 4. 股息率评分
            dy = index_data.get("dividend_yield")
            dy_score, dy_signal, dy_level = self.score_dividend_yield(dy)
            metrics.append({
                "name": "股息率",
                "name_en": "Dividend Yield",
                "value": f"{dy:.2f}%" if dy else "N/A",
                "score": dy_score,
                "signal": dy_signal,
                "level": dy_level,
                "description": "年度股息与股价的比率",
                "benchmark": "历史均值约 1.5-2.0%",
                "stats_10y": pctl.get("dividend"),
                "source_url": "https://www.multpl.com/s-p-500-dividend-yield",
            })

            # 5. MA200 偏离评分
            if ma_data:
                dev = ma_data["deviation"]
                ma_score, ma_signal, ma_level = self.score_ma_deviation(dev)
                metrics.append({
                    "name": "200日均线偏离",
                    "name_en": "200-Day MA Deviation",
                    "value": f"{dev:+.1f}%",
                    "score": ma_score,
                    "signal": ma_signal,
                    "level": ma_level,
                    "description": f"当前价格 ${ma_data['current_price']} vs 均线 ${ma_data['ma200']}",
                    "benchmark": "偏离 ±5% 以内为正常",
                    "stats_10y": pctl.get(f"ma200_{ticker}"),
                    "source_url": f"https://finance.yahoo.com/quote/{ticker}",
                })

            # 6. 风险溢价
            if pe and pe > 0 and treasury:
                earnings_yield = (1 / pe) * 100
                risk_premium = round(earnings_yield - treasury["yield_10y"], 2)
                rp_score, rp_signal, rp_level = self.score_risk_premium(risk_premium)
                metrics.append({
                    "name": "风险溢价",
                    "name_en": "Risk Premium",
                    "value": f"{risk_premium:+.2f}%",
                    "score": rp_score,
                    "signal": rp_signal,
                    "level": rp_level,
                    "description": f"盈利收益率 {earnings_yield:.2f}% - 国债 {treasury['yield_10y']}%",
                    "benchmark": "高于0表示股票相对债券有吸引力",
                })

            # 计算该指数的综合得分（加权平均）
            weights = {
                "市盈率 (PE)": 0.20,
                "周期调整市盈率 (CAPE)": 0.25,
                "市净率 (PB)": 0.10,
                "股息率": 0.10,
                "200日均线偏离": 0.15,
                "风险溢价": 0.20,
            }
            weighted_score = 0
            total_weight = 0
            for m in metrics:
                w = weights.get(m["name"], 0.1)
                weighted_score += m["score"] * w
                total_weight += w
            overall_score = round(weighted_score / total_weight, 1) if total_weight else 50

            signal_text, description, signal_type = self.overall_signal(overall_score)
            all_scores.append(overall_score)

            result["indices"].append({
                "ticker": ticker,
                "name": index_data["name"],
                "name_en": index_data["name_en"],
                "price": index_data["price"],
                "fifty_two_week_high": index_data["fifty_two_week_high"],
                "fifty_two_week_low": index_data["fifty_two_week_low"],
                "metrics": metrics,
                "overall_score": overall_score,
                "signal": signal_text,
                "signal_description": description,
                "signal_type": signal_type,
                "last_updated": index_data["last_updated"],
            })

        # 巴菲特指标
        buffett = self.get_buffett_indicator()
        if buffett:
            bt_score, bt_signal, bt_level = self.score_buffett(buffett["ratio"])
            result["buffett"] = {
                "ratio": buffett["ratio"],
                "gdp_billion": buffett["gdp_billion"],
                "percentile": buffett["percentile"],
                "score": bt_score,
                "signal": bt_signal,
                "level": bt_level,
            }
            all_scores.append(bt_score)

        # 综合信号
        if all_scores:
            avg_score = round(sum(all_scores) / len(all_scores), 1)
            signal_text, description, signal_type = self.overall_signal(avg_score)
            result["overall"] = {
                "score": avg_score,
                "signal": signal_text,
                "description": description,
                "signal_type": signal_type,
            }

        # 获取历史数据用于图表
        result["chart_data"] = {
            "cape_history": self.get_historical_cape(),
            "price_history_spy": self.get_price_history("SPY", "10y"),
            "price_history_qqq": self.get_price_history("QQQ", "10y"),
            "dividend_history": self.get_historical_dividend_yield(),
        }

        return result

    def get_treasury_info(self) -> dict:
        """获取国债收益率信息（用于外部调用）"""
        return self.get_treasury_yield()

    # ==================== 港股数据 ====================

    # 港股指数配置
    HK_INDICES = [
        {"ticker": "^HSI", "name": "恒生指数", "name_en": "Hang Seng Index", "type": "index"},
        {"ticker": "3110.HK", "name": "沪港深红利低波", "name_en": "HS High Div Low Vol ETF", "type": "etf",
         "desc": "追踪恒生高股息低波动指数"},
    ]

    # 港股主要个股
    HK_STOCKS = []

    def get_hk_stock_data(self, ticker: str) -> dict | None:
        """获取港股标的数据"""
        cache_key = f"hk_{ticker}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            t = yf.Ticker(ticker)
            info = t.info or {}

            # 港股价格获取：优先 regularMarketPrice，其次 previousClose
            price = info.get("regularMarketPrice") or info.get("previousClose") or info.get("currentPrice")
            if not price:
                hist = t.history(period="5d")
                if not hist.empty:
                    price = round(float(hist["Close"].iloc[-1]), 2)

            if not price:
                return None

            trailing_pe = info.get("trailingPE")
            forward_pe = info.get("forwardPE")
            pb_ratio = info.get("priceToBook")
            dividend_yield = info.get("dividendYield")
            # yfinance dividendYield 返回的已经是百分比值(如 1.25 = 1.25%)

            fifty_two_week_high = info.get("fiftyTwoWeekHigh")
            fifty_two_week_low = info.get("fiftyTwoWeekLow")

            result = {
                "ticker": ticker,
                "price": round(float(price), 2),
                "trailing_pe": round(trailing_pe, 2) if trailing_pe else None,
                "forward_pe": round(forward_pe, 2) if forward_pe else None,
                "pb_ratio": round(pb_ratio, 2) if pb_ratio else None,
                "dividend_yield": round(dividend_yield, 3) if dividend_yield else None,
                "fifty_two_week_high": round(float(fifty_two_week_high), 2) if fifty_two_week_high else None,
                "fifty_two_week_low": round(float(fifty_two_week_low), 2) if fifty_two_week_low else None,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

            self._set_cached(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"获取港股 {ticker} 数据失败: {e}")
            return None

    # 指数 -> ETF 代理映射（用于补充估值指标）
    HK_INDEX_ETF_MAP = {
        "^HSI": "2800.HK",    # 恒生指数 -> 盈富基金
        "^HSCE": "2828.HK",   # 恒生国企指数 -> 恒生国企ETF
    }

    def _build_hk_metrics(self, ticker: str, data: dict, proxy_ticker: str = None,
                          pctl: dict = None) -> tuple[list, float]:
        """构建港股指标列表和综合评分。proxy_ticker 用于指数的估值代理。"""
        metrics = []
        proxy = None
        if proxy_ticker:
            proxy = self.get_hk_stock_data(proxy_ticker)
        pctl = pctl or {}

        # 确定百分位 key 前缀
        pctl_key = "hsi" if ticker == "^HSI" else "etf_3110"
        yf_ticker = ticker.replace("^", "%5E")  # Yahoo Finance URL encoding
        yf_url = f"https://finance.yahoo.com/quote/{yf_ticker}"

        # PE（优先自身，其次代理）
        pe = data.get("trailing_pe") or (proxy.get("trailing_pe") if proxy else None)
        if pe:
            pe_score, pe_signal, pe_level = self.score_pe(pe)
            src = "" if data.get("trailing_pe") else f" (via {proxy_ticker})"
            metrics.append({
                "name": "市盈率 (PE)", "name_en": "P/E Ratio",
                "value": f"{pe:.1f}", "score": pe_score,
                "signal": pe_signal, "level": pe_level,
                "description": f"股票价格与每股收益的比值{src}",
                "benchmark": "港股历史中位数约 10-12",
                "stats_10y": None,
                "source_url": yf_url,
            })

        # PB
        pb = data.get("pb_ratio") or (proxy.get("pb_ratio") if proxy else None)
        if pb:
            pb_score, pb_signal, pb_level = self.score_pb(pb)
            metrics.append({
                "name": "市净率 (PB)", "name_en": "P/B Ratio",
                "value": f"{pb:.2f}", "score": pb_score,
                "signal": pb_signal, "level": pb_level,
                "description": "股票价格与每股净资产的比值",
                "benchmark": "低于1表示破净",
                "stats_10y": None,
                "source_url": yf_url,
            })

        # 股息率
        dy = data.get("dividend_yield") or (proxy.get("dividend_yield") if proxy else None)
        if dy:
            dy_score, dy_signal, dy_level = self.score_dividend_yield(dy)
            metrics.append({
                "name": "股息率", "name_en": "Dividend Yield",
                "value": f"{dy:.2f}%", "score": dy_score,
                "signal": dy_signal, "level": dy_level,
                "description": "年度股息与股价的比率",
                "benchmark": "港股高股息标的常见 3-5%",
                "stats_10y": None,
                "source_url": yf_url,
            })

        # MA200 偏离
        ma_data = self.get_ma_deviation(ticker)
        if ma_data:
            dev = ma_data["deviation"]
            ma_score, ma_signal, ma_level = self.score_ma_deviation(dev)
            metrics.append({
                "name": "200日均线偏离", "name_en": "200-Day MA Deviation",
                "value": f"{dev:+.1f}%", "score": ma_score,
                "signal": ma_signal, "level": ma_level,
                "description": f"当前价格 {ma_data['current_price']} vs 均线 {ma_data['ma200']}",
                "benchmark": "偏离 ±5% 以内为正常",
                "stats_10y": pctl.get(f"ma200_{pctl_key}"),
                "source_url": yf_url,
            })

        # 综合评分（与美股一致的权重）
        weights = {
            "市盈率 (PE)": 0.20, "周期调整市盈率 (CAPE)": 0.25,
            "市净率 (PB)": 0.10, "股息率": 0.10,
            "200日均线偏离": 0.15, "风险溢价": 0.20,
        }
        if metrics:
            weighted = sum(m["score"] * weights.get(m["name"], 0.15) for m in metrics)
            total_w = sum(weights.get(m["name"], 0.15) for m in metrics)
            overall_score = round(weighted / total_w, 1) if total_w else 50
        else:
            overall_score = 50

        return metrics, overall_score

    def get_hk_dashboard_data(self) -> dict:
        """获取港股仪表盘数据"""
        result = {
            "market": "hk",
            "market_name": "港股",
            "indices": [],
            "stocks": [],
            "overall": None,
            "chart_data": {},
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        all_scores = []

        # 获取港股百分位数据
        hk_pctl = self.get_hk_percentiles()

        # 获取指数和ETF数据
        for idx_conf in self.HK_INDICES:
            data = self.get_hk_stock_data(idx_conf["ticker"])
            if not data:
                continue

            # 对纯指数使用 ETF 代理估值
            proxy_ticker = self.HK_INDEX_ETF_MAP.get(idx_conf["ticker"])

            metrics, overall_score = self._build_hk_metrics(
                idx_conf["ticker"], data, proxy_ticker, pctl=hk_pctl
            )
            signal_text, description, signal_type = self.overall_signal(overall_score)
            all_scores.append(overall_score)

            entry = {
                "ticker": idx_conf["ticker"],
                "name": idx_conf["name"],
                "name_en": idx_conf["name_en"],
                "type": idx_conf["type"],
                "price": data["price"],
                "fifty_two_week_high": data.get("fifty_two_week_high"),
                "fifty_two_week_low": data.get("fifty_two_week_low"),
                "metrics": metrics,
                "overall_score": overall_score,
                "signal": signal_text,
                "signal_description": description,
                "signal_type": signal_type,
                "last_updated": data["last_updated"],
            }
            if idx_conf.get("desc"):
                entry["desc"] = idx_conf["desc"]
            result["indices"].append(entry)

        # 获取个股数据
        for stock_conf in self.HK_STOCKS:
            data = self.get_hk_stock_data(stock_conf["ticker"])
            if not data:
                continue
            result["stocks"].append({
                "ticker": stock_conf["ticker"],
                "name": stock_conf["name"],
                "name_en": stock_conf["name_en"],
                "price": data["price"],
                "trailing_pe": data.get("trailing_pe"),
                "pb_ratio": data.get("pb_ratio"),
                "dividend_yield": data.get("dividend_yield"),
                "fifty_two_week_high": data.get("fifty_two_week_high"),
                "fifty_two_week_low": data.get("fifty_two_week_low"),
            })

        # 综合信号
        if all_scores:
            avg = round(sum(all_scores) / len(all_scores), 1)
            sig, desc, sig_type = self.overall_signal(avg)
            result["overall"] = {"score": avg, "signal": sig, "description": desc, "signal_type": sig_type}

        # 港股图表数据
        result["chart_data"] = {
            "hsi": self.get_price_history("^HSI", "10y"),
            "etf_3110": self.get_price_history("3110.HK", "5y"),
        }

        return result
