"""
A股量化投资面板 - 数据获取器
数据源：yfinance（国际数据接口，支持A股）
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "data"
CACHE_DIR.mkdir(exist_ok=True)

CACHE_TTL = {
    "realtime": 300,
    "daily": 3600,
    "technical": 1800,
    "risk": 3600,
}

# A股指数和ETF的 Yahoo Finance 代码
INDICES = {
    "000300.SS": {"name": "沪深300", "symbol": "000300"},
    "000905.SS": {"name": "中证500", "symbol": "000905"},
    "000016.SS": {"name": "上证50", "symbol": "000016"},
    "000852.SS": {"name": "中证1000", "symbol": "000852"},
    "000510.SS": {"name": "中证A500", "symbol": "000510"},
    "399006.SZ": {"name": "创业板指", "symbol": "399006"},
}

ETFS = {
    "510300.SS": {"name": "沪深300ETF", "benchmark": "沪深300", "category": "broad"},
    "510500.SS": {"name": "中证500ETF", "benchmark": "中证500", "category": "broad"},
    "159915.SZ": {"name": "创业板ETF", "benchmark": "创业板指", "category": "broad"},
    "510050.SS": {"name": "上证50ETF", "benchmark": "上证50", "category": "broad"},
    "512890.SS": {"name": "红利低波ETF", "benchmark": "红利低波", "category": "dividend"},
    "515180.SS": {"name": "红利ETF易方达", "benchmark": "中证红利", "category": "dividend"},
    "510880.SS": {"name": "红利ETF", "benchmark": "上证红利", "category": "dividend"},
}


class AStockDataFetcher:
    def __init__(self):
        self._cache = {}
        self._cache_ts = {}
        self._lock = threading.Lock()

    def _get_cached(self, key: str, ttl_key: str = "daily"):
        with self._lock:
            if key in self._cache:
                if time.time() - self._cache_ts.get(key, 0) < CACHE_TTL.get(ttl_key, 3600):
                    return self._cache[key]
        return None

    def _set_cached(self, key: str, payload):
        with self._lock:
            self._cache[key] = payload
            self._cache_ts[key] = time.time()

    def get_ticker_data(self, symbol: str) -> dict | None:
        """获取单个标的的实时数据"""
        cache_key = f"ticker_{symbol}"
        cached = self._get_cached(cache_key, "realtime")
        if cached:
            return cached

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or "regularMarketPrice" not in info:
                # 尝试从历史数据获取最新价格
                hist = ticker.history(period="5d")
                if hist.empty:
                    return None

                latest = hist.iloc[-1]
                prev_close = hist.iloc[-2]["Close"] if len(hist) > 1 else latest["Open"]

                result = {
                    "symbol": symbol,
                    "name": info.get("shortName", symbol),
                    "price": float(latest["Close"]),
                    "change": float(latest["Close"] - prev_close),
                    "change_pct": float((latest["Close"] - prev_close) / prev_close * 100),
                    "volume": int(latest.get("Volume", 0)),
                    "high": float(latest["High"]),
                    "low": float(latest["Low"]),
                    "open": float(latest["Open"]),
                    "prev_close": float(prev_close),
                    "updated_at": datetime.now().isoformat(),
                }
            else:
                result = {
                    "symbol": symbol,
                    "name": info.get("shortName", symbol),
                    "price": float(info.get("regularMarketPrice", 0)),
                    "change": float(info.get("regularMarketChange", 0)),
                    "change_pct": float(info.get("regularMarketChangePercent", 0)),
                    "volume": int(info.get("regularMarketVolume", 0)),
                    "high": float(info.get("regularMarketDayHigh", 0)),
                    "low": float(info.get("regularMarketDayLow", 0)),
                    "open": float(info.get("regularMarketOpen", 0)),
                    "prev_close": float(info.get("regularMarketPreviousClose", 0)),
                    "updated_at": datetime.now().isoformat(),
                }

            self._set_cached(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"获取 {symbol} 数据失败: {e}")
            return None

    def get_all_indices(self) -> list[dict]:
        """获取所有指数数据"""
        results = []
        for symbol, info in INDICES.items():
            data = self.get_ticker_data(symbol)
            if data:
                data["display_name"] = info["name"]
                data["code"] = info["symbol"]
                results.append(data)
        return results

    def get_all_etfs(self) -> dict:
        """获取所有ETF数据"""
        result = {"broad": [], "dividend": []}
        for symbol, info in ETFS.items():
            data = self.get_ticker_data(symbol)
            if data:
                data["display_name"] = info["name"]
                data["benchmark"] = info["benchmark"]
                result[info["category"]].append(data)
        return result

    def get_history(self, symbol: str, period: str = "1y") -> pd.DataFrame | None:
        """获取历史数据"""
        cache_key = f"history_{symbol}_{period}"
        cached = self._get_cached(cache_key, "daily")
        if cached is not None:
            return pd.DataFrame(cached)

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            if df.empty:
                return None

            self._set_cached(cache_key, df.to_dict(orient="records"))
            return df
        except Exception as e:
            logger.error(f"获取 {symbol} 历史数据失败: {e}")
            return None

    def calculate_rsi(self, prices: pd.Series, period: int = 14) -> float | None:
        """计算RSI"""
        try:
            delta = prices.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return round(float(rsi.iloc[-1]), 2) if not rsi.empty else None
        except:
            return None

    def calculate_macd(self, prices: pd.Series) -> dict | None:
        """计算MACD"""
        try:
            ema12 = prices.ewm(span=12, adjust=False).mean()
            ema26 = prices.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line

            return {
                "macd": round(float(macd_line.iloc[-1]), 4),
                "signal": round(float(signal_line.iloc[-1]), 4),
                "histogram": round(float(histogram.iloc[-1]), 4),
            }
        except:
            return None

    def calculate_bollinger(self, prices: pd.Series, period: int = 20) -> dict | None:
        """计算布林带"""
        try:
            middle = prices.rolling(window=period).mean()
            std = prices.rolling(window=period).std()
            upper = middle + (std * 2)
            lower = middle - (std * 2)

            current = float(prices.iloc[-1])
            bb_upper = float(upper.iloc[-1])
            bb_lower = float(lower.iloc[-1])
            position = (current - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper != bb_lower else 50

            return {
                "upper": round(bb_upper, 2),
                "middle": round(float(middle.iloc[-1]), 2),
                "lower": round(bb_lower, 2),
                "position": round(position, 2),
            }
        except:
            return None

    def calculate_moving_averages(self, prices: pd.Series) -> dict:
        """计算均线"""
        result = {}
        for period in [5, 10, 20, 60, 120, 250]:
            if len(prices) >= period:
                ma = prices.rolling(window=period).mean()
                result[f"ma{period}"] = round(float(ma.iloc[-1]), 2) if not ma.empty else None
            else:
                result[f"ma{period}"] = None
        return result

    def calculate_volatility(self, prices: pd.Series, period: int = 20) -> dict | None:
        """计算波动率"""
        try:
            returns = prices.pct_change().dropna()
            hist_vol = returns.rolling(window=period).std() * np.sqrt(252) * 100
            current_vol = float(hist_vol.iloc[-1])
            percentile = (hist_vol < current_vol).sum() / len(hist_vol) * 100

            return {
                "current": round(current_vol, 2),
                "percentile": round(percentile, 2),
            }
        except:
            return None

    def calculate_risk_metrics(self, prices: pd.Series, risk_free_rate: float = 0.02) -> dict | None:
        """计算风险指标"""
        try:
            returns = prices.pct_change().dropna()

            total_return = (prices.iloc[-1] / prices.iloc[0] - 1)
            years = len(prices) / 252
            annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

            annual_vol = returns.std() * np.sqrt(252)
            sharpe = (annual_return - risk_free_rate) / annual_vol if annual_vol > 0 else 0

            cumulative = (1 + returns).cumprod()
            running_max = cumulative.expanding().max()
            drawdown = (cumulative - running_max) / running_max
            max_drawdown = drawdown.min()

            calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
            win_rate = (returns > 0).sum() / len(returns) * 100

            avg_win = returns[returns > 0].mean() if (returns > 0).any() else 0
            avg_loss = abs(returns[returns < 0].mean()) if (returns < 0).any() else 1
            profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

            return {
                "annual_return": round(annual_return * 100, 2),
                "annual_volatility": round(annual_vol * 100, 2),
                "sharpe_ratio": round(sharpe, 2),
                "max_drawdown": round(max_drawdown * 100, 2),
                "calmar_ratio": round(calmar, 2),
                "win_rate": round(win_rate, 2),
                "profit_loss_ratio": round(profit_loss_ratio, 2),
                "total_return": round(total_return * 100, 2),
            }
        except Exception as e:
            logger.error(f"计算风险指标失败: {e}")
            return None

    def get_technical_analysis(self, symbol: str) -> dict | None:
        """获取完整技术分析"""
        cache_key = f"tech_{symbol}"
        cached = self._get_cached(cache_key, "technical")
        if cached:
            return cached

        try:
            df = self.get_history(symbol, "1y")
            if df is None or df.empty:
                return None

            prices = df["Close"]
            current_price = float(prices.iloc[-1])

            # RSI
            rsi = self.calculate_rsi(prices)
            rsi_signal = "超买" if rsi and rsi > 70 else ("超卖" if rsi and rsi < 30 else "中性")

            # MACD
            macd = self.calculate_macd(prices)
            macd_signal = "金叉" if macd and macd["macd"] > macd["signal"] else "死叉"

            # 布林带
            bollinger = self.calculate_bollinger(prices)
            bb_signal = "接近上轨" if bollinger and bollinger["position"] > 80 else (
                "接近下轨" if bollinger and bollinger["position"] < 20 else "通道内"
            )

            # 均线
            ma = self.calculate_moving_averages(prices)

            # 波动率
            volatility = self.calculate_volatility(prices)

            result = {
                "symbol": symbol,
                "current_price": current_price,
                "rsi": {"value": rsi, "signal": rsi_signal},
                "macd": {**macd, "signal_text": macd_signal} if macd else None,
                "bollinger": {**bollinger, "signal": bb_signal} if bollinger else None,
                "moving_averages": ma,
                "volatility": volatility,
                "updated_at": datetime.now().isoformat(),
            }

            self._set_cached(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"获取 {symbol} 技术分析失败: {e}")
            return None

    def get_dashboard_data(self) -> dict:
        """获取完整面板数据"""
        data = {
            "indices": [],
            "etfs": {"broad": [], "dividend": []},
            "technical": {},
            "risk_metrics": {},
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        logger.info("获取指数数据...")
        data["indices"] = self.get_all_indices()

        logger.info("获取ETF数据...")
        data["etfs"] = self.get_all_etfs()

        logger.info("计算技术指标...")
        for symbol in ["000300.SS", "000905.SS"]:
            tech = self.get_technical_analysis(symbol)
            if tech:
                data["technical"][symbol] = tech

        logger.info("计算风险指标...")
        for symbol in ["000300.SS", "000905.SS"]:
            df = self.get_history(symbol, "1y")
            if df is not None and not df.empty:
                risk = self.calculate_risk_metrics(df["Close"])
                if risk:
                    data["risk_metrics"][symbol] = risk

        return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetcher = AStockDataFetcher()
    data = fetcher.get_dashboard_data()
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
