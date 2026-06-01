"""
Unified Market Dashboard - Flask App
美股 / 港股 / A股 统一估值仪表盘
端口: 8082
"""

import logging
import threading
from flask import Flask, render_template, jsonify, redirect, url_for
from data_fetcher import MarketDataFetcher
from a_stock_fetcher import AStockDataFetcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 全局数据获取器
us_fetcher = MarketDataFetcher()
cn_fetcher = AStockDataFetcher()


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
        # 统一键名：fetcher 返回 "dividend"，模板用 "dividend_low_vol"
        if "dividend" in data.get("etfs", {}):
            data["etfs"]["dividend_low_vol"] = data["etfs"].pop("dividend")
        return render_template("unified.html", data=data, active_market="cn")
    except Exception as e:
        logger.error(f"渲染A股面板失败: {e}")
        return render_template("unified.html", data={
            "indices": [],
            "etfs": {"dividend_low_vol": [], "broad": []},
            "technical": {}, "risk_metrics": {},
            "last_updated": "数据加载中...", "error": str(e),
        }, active_market="cn")


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
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "unified-market-dashboard"})


if __name__ == "__main__":
    init_background_cache()
    app.run(host="0.0.0.0", port=8082, debug=False)
