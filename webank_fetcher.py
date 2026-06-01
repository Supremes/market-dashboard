"""
微众银行产品数据加载器
从 JSON 配置文件读取产品数据
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "webank_products.json")


def load_products():
    """加载微众银行产品数据"""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        logger.error(f"产品数据文件不存在: {DATA_FILE}")
        return _empty_data()
    except Exception as e:
        logger.error(f"加载产品数据失败: {e}")
        return _empty_data()


def get_dashboard_data():
    """获取微众银行仪表盘数据"""
    data = load_products()

    # 计算每个分类的统计信息
    for cat_name, cat in data["categories"].items():
        products = cat["products"]
        if products:
            cat["count"] = len(products)
            cat["avg_rate_7d"] = round(sum(p["rate_7d"] for p in products) / len(products), 2)
            cat["avg_rate_1y"] = round(sum(p["rate_1y"] for p in products) / len(products), 2)
            cat["max_rate_7d"] = max(p["rate_7d"] for p in products)
            cat["min_rate_7d"] = min(p["rate_7d"] for p in products)
            # 排序
            cat["ranked_by_7d"] = sorted(products, key=lambda x: x["rate_7d"], reverse=True)
            cat["ranked_by_1y"] = sorted(products, key=lambda x: x["rate_1y"], reverse=True)

    # 全部产品排行
    all_products = []
    for cat_name, cat in data["categories"].items():
        for p in cat["products"]:
            p["category"] = cat_name
            p["category_icon"] = cat["icon"]
            all_products.append(p)

    data["all_products"] = sorted(all_products, key=lambda x: x["rate_7d"], reverse=True)
    data["total_count"] = len(all_products)

    # 整体统计
    if all_products:
        data["overall_avg_7d"] = round(sum(p["rate_7d"] for p in all_products) / len(all_products), 2)
        data["overall_avg_1y"] = round(sum(p["rate_1y"] for p in all_products) / len(all_products), 2)
        data["best_product"] = max(all_products, key=lambda x: x["rate_7d"])

    return data


def _empty_data():
    return {
        "last_updated": "无数据",
        "categories": {},
        "all_products": [],
        "total_count": 0,
        "overall_avg_7d": 0,
        "overall_avg_1y": 0,
    }
