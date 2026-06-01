"""
A股量化投资面板 - Flask 应用
端口: 8082
"""

import logging
import threading
from flask import Flask, render_template, jsonify
from a_stock_fetcher import AStockDataFetcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
fetcher = AStockDataFetcher()


def init_background_cache():
    """后台线程预加载缓存数据"""
    def _load():
        try:
            logger.info("开始后台预加载A股数据...")
            fetcher.get_dashboard_data()
            logger.info("A股数据预加载完成")
        except Exception as e:
            logger.error(f"后台预加载失败: {e}")

    thread = threading.Thread(target=_load, daemon=True)
    thread.start()


@app.route("/")
def dashboard():
    """主面板页面"""
    try:
        data = fetcher.get_dashboard_data()
        return render_template("a_stock_index.html", data=data)
    except Exception as e:
        logger.error(f"渲染面板失败: {e}")
        return render_template("a_stock_index.html", data={
            "indices": [],
            "etfs": {"dividend_low_vol": [], "broad": []},
            "technical": {},
            "risk_metrics": {},
            "last_updated": "数据加载中...",
            "error": str(e),
        })


@app.route("/api/data")
def api_data():
    """API: 返回 JSON 数据"""
    try:
        data = fetcher.get_dashboard_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/technical/<symbol>")
def api_technical(symbol):
    """API: 获取技术指标"""
    try:
        is_etf = len(symbol) == 6 and symbol.startswith(("1", "5"))
        data = fetcher.get_technical_indicators(symbol, is_etf=is_etf)
        if data:
            return jsonify(data)
        return jsonify({"error": "数据获取失败"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/risk/<symbol>")
def api_risk(symbol):
    """API: 获取风险指标"""
    try:
        df = fetcher.get_index_daily(symbol, days=365)
        if df is not None and not df.empty:
            data = fetcher.calculate_risk_metrics(df["close"])
            return jsonify(data)
        return jsonify({"error": "数据获取失败"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """API: 强制刷新数据"""
    try:
        with fetcher._lock:
            fetcher._cache.clear()
            fetcher._cache_ts.clear()

        data = fetcher.get_dashboard_data()
        return jsonify({"status": "ok", "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "a-stock-dashboard"})


if __name__ == "__main__":
    init_background_cache()
    app.run(host="0.0.0.0", port=8082, debug=False)
