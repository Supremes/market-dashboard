"""
Unified Market Dashboard - Flask App
美股 / 港股 / A股 估值仪表盘 + 量化模拟操盘
端口: 8082
"""

import logging
import threading
from flask import Flask, render_template, jsonify, redirect, request
from data_fetcher import MarketDataFetcher
from a_stock_fetcher import AStockDataFetcher
from trading_engine import TradingEngine
from webank_fetcher import get_dashboard_data as get_webank_data

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 全局数据获取器
us_fetcher = MarketDataFetcher()
cn_fetcher = AStockDataFetcher()
trading = TradingEngine()
webank_data_cache = {"data": None, "ts": 0}


def init_background_cache():
    """后台线程预加载缓存数据"""
    def _load():
        try:
            logger.info("开始后台预加载美股/港股数据...")
            us_fetcher.get_dashboard_data()
            logger.info("美股数据预加载完成")
        except Exception as e:
            logger.error(f"美股数据预加载失败: {e}")

        try:
            logger.info("开始后台预加载A股数据...")
            cn_fetcher.get_dashboard_data()
            logger.info("A股数据预加载完成")
        except Exception as e:
            logger.error(f"A股数据预加载失败: {e}")

    thread = threading.Thread(target=_load, daemon=True)
    thread.start()


@app.route("/")
def index():
    """首页重定向到美股"""
    return redirect("/us")


@app.route("/us")
def dashboard_us():
    """美股估值仪表盘"""
    try:
        data = us_fetcher.get_dashboard_data()
        data["market"] = "us"
        data["market_name"] = "美股"
        return render_template("unified.html", data=data, active_market="us")
    except Exception as e:
        logger.error(f"渲染美股仪表盘失败: {e}")
        return render_template("unified.html", data={
            "market": "us", "market_name": "美股",
            "indices": [], "buffett": None, "overall": None,
            "chart_data": {}, "last_updated": "数据加载中...", "error": str(e),
        }, active_market="us")


@app.route("/hk")
def dashboard_hk():
    """港股仪表盘"""
    try:
        data = us_fetcher.get_hk_dashboard_data()
        return render_template("unified.html", data=data, active_market="hk")
    except Exception as e:
        logger.error(f"渲染港股仪表盘失败: {e}")
        return render_template("unified.html", data={
            "market": "hk", "market_name": "港股",
            "indices": [], "stocks": [], "overall": None,
            "chart_data": {}, "last_updated": "数据加载中...", "error": str(e),
        }, active_market="hk")


@app.route("/cn")
def dashboard_cn():
    """A股量化投资面板"""
    try:
        data = cn_fetcher.get_dashboard_data()
        if "dividend" in data.get("etfs", {}):
            data["etfs"]["dividend_low_vol"] = data["etfs"].pop("dividend")
        data["market"] = "cn"
        data["market_name"] = "A股"
        return render_template("unified.html", data=data, active_market="cn")
    except Exception as e:
        logger.error(f"渲染A股面板失败: {e}")
        return render_template("unified.html", data={
            "market": "cn", "market_name": "A股",
            "indices": [],
            "etfs": {"dividend_low_vol": [], "broad": []},
            "technical": {}, "risk_metrics": {},
            "last_updated": "数据加载中...", "error": str(e),
        }, active_market="cn")


@app.route("/trading")
def trading_dashboard():
    """量化模拟操盘面板"""
    try:
        years = float(request.args.get("years", 3))
        market = request.args.get("market", "all")

        if market == "all":
            data = trading.get_all_markets(years)
            comparison = trading.get_strategy_comparison(years)
        else:
            data = {market: trading.run_market_backtest(market, years)}
            comparison = trading.get_strategy_comparison(years)

        return render_template("trading.html",
                               data=data, comparison=comparison,
                               years=years, active_market="trading")
    except Exception as e:
        logger.error(f"渲染操盘面板失败: {e}")
        import traceback
        traceback.print_exc()
        return render_template("trading.html",
                               data={}, comparison={"strategies": []},
                               years=3, active_market="trading",
                               error=str(e))


@app.route("/api/trading")
def api_trading():
    """API: 操盘数据 JSON"""
    try:
        years = float(request.args.get("years", 3))
        market = request.args.get("market", "all")

        if market == "all":
            data = trading.get_all_markets(years)
        else:
            data = {market: trading.run_market_backtest(market, years)}

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/comparison")
def api_comparison():
    """API: 策略对比数据"""
    try:
        years = float(request.args.get("years", 3))
        data = trading.get_strategy_comparison(years)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """API: 强制刷新所有数据"""
    try:
        with us_fetcher._lock:
            us_fetcher._cache.clear()
            us_fetcher._cache_ts.clear()
        with cn_fetcher._lock:
            cn_fetcher._cache.clear()
            cn_fetcher._cache_ts.clear()
        trading.clear_cache()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "unified-market-dashboard"})


@app.route("/webank")
def dashboard_webank():
    """微众银行理财产品收益排行"""
    import time
    try:
        if webank_data_cache["data"] is None or time.time() - webank_data_cache["ts"] > 300:
            webank_data_cache["data"] = get_webank_data()
            webank_data_cache["ts"] = time.time()
        return render_template("webank.html", data=webank_data_cache["data"], active_market="webank")
    except Exception as e:
        logger.error(f"渲染微众银行面板失败: {e}")
        return render_template("webank.html", data={
            "last_updated": "加载失败", "categories": {}, "all_products": [],
            "total_count": 0, "overall_avg_7d": 0, "overall_avg_1y": 0,
        }, active_market="webank")


@app.route("/api/webank")
def api_webank():
    """API: 微众银行产品数据 JSON"""
    try:
        return jsonify(get_webank_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_background_cache()
    app.run(host="0.0.0.0", port=8082, debug=False)
