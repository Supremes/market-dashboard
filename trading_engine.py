"""
量化投资模拟引擎
三市场（A股/港股/美股）× 多策略模拟操盘
"""

import json
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CACHE_TTL = 3600  # 1 hour

# ═══════════════════════════════════════════════════════
# 三个市场的标的配置
# ═══════════════════════════════════════════════════════
MARKETS = {
    "cn": {
        "name": "A股",
        "flag": "🇨🇳",
        "instruments": {
            "510300.SS": {"name": "沪深300ETF", "short": "300ETF"},
            "510500.SS": {"name": "中证500ETF", "short": "500ETF"},
            "512890.SS": {"name": "红利低波ETF", "short": "红利低波"},
        },
        "initial_capital": 100000,  # ¥10万
        "currency": "¥",
    },
    "hk": {
        "name": "港股",
        "flag": "🇭🇰",
        "instruments": {
            "2800.HK": {"name": "盈富基金(恒指)", "short": "恒指ETF"},
            "3067.HK": {"name": "恒生科技ETF", "short": "科技ETF"},
            "2828.HK": {"name": "恒生H股ETF", "short": "H股ETF"},
        },
        "initial_capital": 100000,  # HK$10万
        "currency": "HK$",
    },
    "us": {
        "name": "美股",
        "flag": "🇺🇸",
        "instruments": {
            "SPY": {"name": "标普500 ETF", "short": "SPY"},
            "QQQ": {"name": "纳斯达克100 ETF", "short": "QQQ"},
        },
        "initial_capital": 10000,  # $1万
        "currency": "$",
    },
}


# ═══════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════
class HistoricalDataLoader:
    """加载并缓存历史数据"""

    def __init__(self):
        self._cache = {}
        self._cache_ts = {}
        self._lock = threading.Lock()

    def get_history(self, symbol: str, years: float = 3) -> pd.DataFrame:
        cache_key = f"{symbol}_{years}y"
        with self._lock:
            if cache_key in self._cache:
                if time.time() - self._cache_ts.get(cache_key, 0) < CACHE_TTL:
                    return self._cache[cache_key]

        try:
            ticker = yf.Ticker(symbol)
            period = f"{int(years * 365)}d"
            df = ticker.history(period=period, auto_adjust=True)
            if df.empty:
                # try max
                df = ticker.history(period="max", auto_adjust=True)
                cutoff = datetime.now() - timedelta(days=int(years * 365))
                df = df[df.index >= cutoff]

            if not df.empty:
                with self._lock:
                    self._cache[cache_key] = df
                    self._cache_ts[cache_key] = time.time()
            return df
        except Exception as e:
            logger.error(f"获取 {symbol} 历史数据失败: {e}")
            return pd.DataFrame()


# ═══════════════════════════════════════════════════════
# 策略基类与具体策略
# ═══════════════════════════════════════════════════════
class Strategy:
    """策略基类"""
    name: str = "基础策略"
    strategy_type: str = "mixed"  # "short_term" | "long_term"
    description: str = ""

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """返回信号序列: 1=买入, -1=卖出, 0=持有"""
        raise NotImplementedError


class DualMA(Strategy):
    """双均线交叉策略 (短期)"""
    name = "双均线交叉"
    strategy_type = "short_term"
    description = "MA20上穿MA60买入，下穿卖出。适合趋势行情。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ma20 = df["Close"].rolling(20).mean()
        ma60 = df["Close"].rolling(60).mean()
        signals = pd.Series(0, index=df.index)
        signals[ma20 > ma60] = 1
        signals[ma20 <= ma60] = -1
        # 只在交叉点触发
        return signals.diff().clip(-1, 1).fillna(0).astype(int)


class RSIMeanReversion(Strategy):
    """RSI均值回归 (短期)"""
    name = "RSI均值回归"
    strategy_type = "short_term"
    description = "RSI<30买入，RSI>70卖出。适合震荡行情。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        signals = pd.Series(0, index=df.index)
        signals[rsi < 30] = 1
        signals[rsi > 70] = -1
        return signals


class MACDMomentum(Strategy):
    """MACD动量策略 (短期)"""
    name = "MACD动量"
    strategy_type = "short_term"
    description = "MACD金叉买入，死叉卖出。跟随动量。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ema12 = df["Close"].ewm(span=12).mean()
        ema26 = df["Close"].ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        signals = pd.Series(0, index=df.index)
        signals[macd > signal] = 1
        signals[macd <= signal] = -1
        return signals.diff().clip(-1, 1).fillna(0).astype(int)


class BollingerBreakout(Strategy):
    """布林带策略 (短期)"""
    name = "布林带突破"
    strategy_type = "short_term"
    description = "价格触及下轨买入，触及上轨卖出。均值回归型。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        mid = df["Close"].rolling(20).mean()
        std = df["Close"].rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        signals = pd.Series(0, index=df.index)
        signals[df["Close"] < lower] = 1
        signals[df["Close"] > upper] = -1
        return signals


class TrendFollowing(Strategy):
    """趋势跟踪 (长期)"""
    name = "趋势跟踪"
    strategy_type = "long_term"
    description = "价格在200日均线上方持有，下方清仓。最简单的趋势策略。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ma200 = df["Close"].rolling(200).mean()
        signals = pd.Series(0, index=df.index)
        signals[df["Close"] > ma200] = 1
        signals[df["Close"] <= ma200] = -1
        return signals.diff().clip(-1, 1).fillna(0).astype(int)


class ValueAveraging(Strategy):
    """价值平均定投 (长期)"""
    name = "价值平均定投"
    strategy_type = "long_term"
    description = "每月定投，价格低于均线时加倍投入，高于均线时减半。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ma120 = df["Close"].rolling(120).mean()
        signals = pd.Series(0, index=df.index)
        # 每月第一个交易日触发
        monthly = df.resample("MS").first().index
        for date in monthly:
            if date in df.index:
                if df.loc[date, "Close"] < ma120.loc[date] * 0.95:
                    signals.loc[date] = 2  # 双倍买入
                elif df.loc[date, "Close"] > ma120.loc[date] * 1.10:
                    signals.loc[date] = -1  # 减仓
                else:
                    signals.loc[date] = 1  # 正常定投
        return signals


class MomentumRotation(Strategy):
    """动量轮动 (长期)"""
    name = "动量轮动"
    strategy_type = "long_term"
    description = "持有过去3个月涨幅最大的标的，每月调仓。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        returns_3m = df["Close"].pct_change(60)
        ma20 = df["Close"].rolling(20).mean()
        signals = pd.Series(0, index=df.index)
        # 每月第一个交易日
        monthly = df.resample("MS").first().index
        for date in monthly:
            if date in df.index and not pd.isna(returns_3m.loc[date]):
                if returns_3m.loc[date] > 0 and df.loc[date, "Close"] > ma20.loc[date]:
                    signals.loc[date] = 1
                elif returns_3m.loc[date] < -0.05:
                    signals.loc[date] = -1
        return signals


class KeltnerChannel(Strategy):
    """肯特纳通道 (短期)"""
    name = "肯特纳通道"
    strategy_type = "short_term"
    description = "EMA20 ± 2×ATR 通道，突破上轨做多，跌破下轨做空。"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ema20 = df["Close"].ewm(span=20).mean()
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift()).abs(),
            (df["Low"] - df["Close"].shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        upper = ema20 + 2 * atr
        lower = ema20 - 2 * atr
        signals = pd.Series(0, index=df.index)
        signals[df["Close"] > upper] = 1
        signals[df["Close"] < lower] = -1
        return signals


# 所有策略
ALL_STRATEGIES = [
    DualMA(),
    RSIMeanReversion(),
    MACDMomentum(),
    BollingerBreakout(),
    KeltnerChannel(),
    TrendFollowing(),
    ValueAveraging(),
    MomentumRotation(),
]


# ═══════════════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════════════
class BacktestResult:
    def __init__(self):
        self.trades = []
        self.equity_curve = []
        self.daily_returns = []
        self.metrics = {}


def run_backtest(df: pd.DataFrame, strategy: Strategy, initial_capital: float = 100000,
                 commission: float = 0.001) -> BacktestResult:
    """运行回测"""
    result = BacktestResult()
    if df.empty or len(df) < 60:
        return result

    signals = strategy.generate_signals(df)
    prices = df["Close"]

    capital = initial_capital
    position = 0  # 持仓股数
    equity = []
    trades = []
    entry_price = 0

    for i, (date, price) in enumerate(prices.items()):
        sig = signals.get(date, 0) if date in signals.index else 0
        price_val = float(price)

        if sig > 0 and position == 0:
            # 买入
            shares = int(capital * 0.95 / price_val)  # 留5%现金
            if shares > 0:
                cost = shares * price_val * (1 + commission)
                capital -= cost
                position = shares
                entry_price = price_val
                trades.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "action": "买入",
                    "price": round(price_val, 3),
                    "shares": shares,
                    "cost": round(cost, 2),
                })

        elif sig < 0 and position > 0:
            # 卖出
            revenue = position * price_val * (1 - commission)
            pnl = revenue - position * entry_price
            capital += revenue
            trades.append({
                "date": date.strftime("%Y-%m-%d"),
                "action": "卖出",
                "price": round(price_val, 3),
                "shares": position,
                "revenue": round(revenue, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
            })
            position = 0

        # 计算当日净值
        total_value = capital + position * price_val
        equity.append({
            "date": date.strftime("%Y-%m-%d"),
            "value": round(total_value, 2),
        })

    result.trades = trades
    result.equity_curve = equity

    # 计算指标
    if equity:
        values = pd.Series([e["value"] for e in equity])
        returns = values.pct_change().dropna()
        total_return = (values.iloc[-1] / initial_capital - 1) * 100
        years = len(values) / 252
        annual_return = ((values.iloc[-1] / initial_capital) ** (1 / max(years, 0.01)) - 1) * 100
        annual_vol = returns.std() * np.sqrt(252) * 100 if len(returns) > 1 else 0
        sharpe = (annual_return / 100 - 0.02) / (annual_vol / 100) if annual_vol > 0 else 0

        # 最大回撤
        cummax = values.expanding().max()
        drawdown = (values - cummax) / cummax
        max_drawdown = drawdown.min() * 100

        # 胜率
        if trades:
            sell_trades = [t for t in trades if t["action"] == "卖出"]
            wins = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
            win_rate = wins / len(sell_trades) * 100 if sell_trades else 0
            total_trades = len(sell_trades)
        else:
            win_rate = 0
            total_trades = 0

        # 最终持仓价值
        final_position_value = position * float(prices.iloc[-1]) if position > 0 else 0

        result.metrics = {
            "total_return": round(total_return, 2),
            "annual_return": round(annual_return, 2),
            "annual_volatility": round(annual_vol, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_drawdown, 2),
            "win_rate": round(win_rate, 1),
            "total_trades": total_trades,
            "final_value": round(float(values.iloc[-1]), 2),
            "initial_capital": initial_capital,
            "cash": round(capital, 2),
            "position_value": round(final_position_value, 2),
            "position_shares": position,
        }

    return result


# ═══════════════════════════════════════════════════════
# Buy & Hold 基准
# ═══════════════════════════════════════════════════════
def run_buy_and_hold(df: pd.DataFrame, initial_capital: float = 100000,
                     commission: float = 0.001) -> BacktestResult:
    """买入持有基准"""
    result = BacktestResult()
    if df.empty:
        return result

    prices = df["Close"]
    first_price = float(prices.iloc[0])
    shares = int(initial_capital * 0.95 / first_price)
    cost = shares * first_price * (1 + commission)
    remaining = initial_capital - cost

    equity = []
    for date, price in prices.items():
        total = remaining + shares * float(price)
        equity.append({"date": date.strftime("%Y-%m-%d"), "value": round(total, 2)})

    final_value = equity[-1]["value"]
    years = len(equity) / 252
    total_return = (final_value / initial_capital - 1) * 100
    annual_return = ((final_value / initial_capital) ** (1 / max(years, 0.01)) - 1) * 100

    values = pd.Series([e["value"] for e in equity])
    returns = values.pct_change().dropna()
    annual_vol = returns.std() * np.sqrt(252) * 100 if len(returns) > 1 else 0
    sharpe = (annual_return / 100 - 0.02) / (annual_vol / 100) if annual_vol > 0 else 0
    cummax = values.expanding().max()
    max_drawdown = ((values - cummax) / cummax).min() * 100

    result.equity_curve = equity
    result.metrics = {
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "annual_volatility": round(annual_vol, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_drawdown, 2),
        "win_rate": 100 if total_return > 0 else 0,
        "total_trades": 1,
        "final_value": round(final_value, 2),
        "initial_capital": initial_capital,
        "cash": round(remaining, 2),
        "position_value": round(shares * float(prices.iloc[-1]), 2),
        "position_shares": shares,
    }
    return result


# ═══════════════════════════════════════════════════════
# 主引擎
# ═══════════════════════════════════════════════════════
class TradingEngine:
    """模拟操盘引擎"""

    def __init__(self):
        self.loader = HistoricalDataLoader()
        self._cache = {}
        self._cache_ts = {}
        self._lock = threading.Lock()
        self._strategies = ALL_STRATEGIES

    def _get_cached(self, key: str):
        with self._lock:
            if key in self._cache:
                if time.time() - self._cache_ts.get(key, 0) < CACHE_TTL:
                    return self._cache[key]
        return None

    def _set_cached(self, key: str, data):
        with self._lock:
            self._cache[key] = data
            self._cache_ts[key] = time.time()

    def run_market_backtest(self, market_key: str, years: float = 3) -> dict:
        """对一个市场运行所有策略回测"""
        cache_key = f"backtest_{market_key}_{years}y"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        market = MARKETS[market_key]
        results = {
            "market": market_key,
            "market_name": market["name"],
            "flag": market["flag"],
            "currency": market["currency"],
            "initial_capital": market["initial_capital"],
            "strategies": [],
            "instruments": {},
        }

        for symbol, info in market["instruments"].items():
            df = self.loader.get_history(symbol, years)
            if df.empty:
                logger.warning(f"无法获取 {symbol} 数据，跳过")
                continue

            inst_results = {
                "symbol": symbol,
                "name": info["name"],
                "short": info["short"],
                "data_start": df.index[0].strftime("%Y-%m-%d"),
                "data_end": df.index[-1].strftime("%Y-%m-%d"),
                "current_price": round(float(df["Close"].iloc[-1]), 3),
                "strategies": {},
            }

            # Buy & Hold 基准
            bh = run_buy_and_hold(df, market["initial_capital"])
            inst_results["buy_and_hold"] = {
                "name": "买入持有 (基准)",
                "strategy_type": "benchmark",
                "description": "第一天全仓买入并持有至今。",
                "metrics": bh.metrics,
                "equity_curve": bh.equity_curve[::5],  # 每5天取一个点，减小数据量
                "trades": [],
            }

            # 各策略
            for strat in self._strategies:
                result = run_backtest(df, strat, market["initial_capital"])
                inst_results["strategies"][strat.name] = {
                    "name": strat.name,
                    "strategy_type": strat.strategy_type,
                    "description": strat.description,
                    "metrics": result.metrics,
                    "equity_curve": result.equity_curve[::5],
                    "trades": result.trades[-20:],  # 只保留最近20笔交易
                }

            results["instruments"][symbol] = inst_results

        self._set_cached(cache_key, results)
        return results

    def get_all_markets(self, years: float = 3) -> dict:
        """获取所有市场的回测结果"""
        all_results = {}
        for key in MARKETS:
            try:
                all_results[key] = self.run_market_backtest(key, years)
            except Exception as e:
                logger.error(f"市场 {key} 回测失败: {e}")
                all_results[key] = {"error": str(e)}
        return all_results

    def get_strategy_comparison(self, years: float = 3) -> dict:
        """跨市场策略对比"""
        cache_key = f"comparison_{years}y"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        comparison = {
            "strategies": [],
            "years": years,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        strategy_summaries = {}
        for strat in [None] + self._strategies:  # None = buy & hold
            name = "买入持有" if strat is None else strat.name
            stype = "benchmark" if strat is None else strat.strategy_type
            desc = "第一天全仓买入并持有至今。" if strat is None else strat.description

            market_results = {}
            for mkey, market in MARKETS.items():
                m_returns = []
                for symbol in market["instruments"]:
                    df = self.loader.get_history(symbol, years)
                    if df.empty:
                        continue
                    if strat is None:
                        r = run_buy_and_hold(df, market["initial_capital"])
                    else:
                        r = run_backtest(df, strat, market["initial_capital"])
                    if r.metrics:
                        m_returns.append(r.metrics)

                if m_returns:
                    avg_return = np.mean([m["total_return"] for m in m_returns])
                    avg_sharpe = np.mean([m["sharpe_ratio"] for m in m_returns])
                    avg_mdd = np.mean([m["max_drawdown"] for m in m_returns])
                    avg_annual = np.mean([m["annual_return"] for m in m_returns])
                    market_results[mkey] = {
                        "avg_return": round(float(avg_return), 2),
                        "avg_annual_return": round(float(avg_annual), 2),
                        "avg_sharpe": round(float(avg_sharpe), 2),
                        "avg_max_drawdown": round(float(avg_mdd), 2),
                        "num_instruments": len(m_returns),
                    }

            # 全市场综合
            all_returns = []
            all_sharpes = []
            for mr in market_results.values():
                all_returns.append(mr["avg_return"])
                all_sharpes.append(mr["avg_sharpe"])

            strategy_summaries[name] = {
                "name": name,
                "strategy_type": stype,
                "description": desc,
                "markets": market_results,
                "overall_avg_return": round(float(np.mean(all_returns)), 2) if all_returns else 0,
                "overall_avg_sharpe": round(float(np.mean(all_sharpes)), 2) if all_sharpes else 0,
            }

        comparison["strategies"] = sorted(
            strategy_summaries.values(),
            key=lambda x: x["overall_avg_return"],
            reverse=True,
        )

        self._set_cached(cache_key, comparison)
        return comparison

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self._cache_ts.clear()


# ═══════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = TradingEngine()

    # 测试单个市场
    result = engine.run_market_backtest("cn")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])
