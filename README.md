# 📊 Market Valuation Dashboard

美股 / 港股 / A股 估值仪表盘 + 量化模拟操盘

## ✨ 功能特性

- **多市场覆盖**：美股（S&P 500 / NASDAQ）、港股（恒生 / 恒生国企）、A股（沪深300 / 中证500 / 创业板）
- **估值指标**：PE / PB / ROE / 股息率 / CAPE 席勒市盈率
- **历史趋势**：10 年 PE/PB 走势图、价格叠加对比
- **定投计算器**：基于估值百分位的智能定投建议
- **量化操盘**：模拟交易引擎，支持买卖记录和收益统计
- **微众银行**：理财产品数据聚合展示
- **AI 分析**：接入 LLM 对市场数据进行智能解读

## 🛠 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3 + Flask |
| 数据源 | yfinance（美股/港股）、AKShare（A股）、FRED（国债收益率）、multpl.com（CAPE） |
| 前端 | Jinja2 + Chart.js + 响应式暗色主题 |
| 存储 | JSON 文件缓存（`data/` 目录） |
| 部署 | systemd 服务，端口 8082 |

## 📦 安装

```bash
# 克隆仓库
git clone https://github.com/Supremes/market-dashboard.git
cd market-dashboard

# 安装依赖
pip install flask yfinance akshare requests

# 运行
python app.py
```

访问 `http://localhost:8082`

## 🗂 项目结构

```
market-dashboard/
├── app.py                 # Flask 主应用
├── data_fetcher.py        # 美股/港股数据获取
├── a_stock_fetcher.py     # A股数据获取（AKShare）
├── trading_engine.py      # 量化交易引擎
├── webank_fetcher.py      # 微众银行数据
├── data/                  # 缓存数据（JSON）
│   ├── cape.json          # CAPE 席勒市盈率
│   ├── treasury.json      # 美国国债收益率
│   ├── pe_hist.json       # PE 历史数据
│   └── ...
├── templates/
│   ├── index.html         # 首页（美股仪表盘）
│   ├── unified.html       # 统一对比视图
│   ├── trading.html       # 量化操盘页面
│   ├── webank.html        # 微众银行页面
│   └── ...
└── static/
```

## 🔌 路由说明

| 路由 | 说明 |
|------|------|
| `/` | 首页（美股估值仪表盘） |
| `/us` | 美股详细数据 |
| `/hk` | 港股详细数据 |
| `/cn` | A股详细数据 |
| `/trading` | 量化模拟操盘 |
| `/webank` | 微众银行理财 |
| `/api/trading` | 交易 API |
| `/api/comparison` | 市场对比 API |
| `/api/refresh` | 手动刷新数据 |
| `/health` | 健康检查 |

## ⚙️ 配置

环境变量（可选）：
- `OPENAI_API_KEY`：AI 分析功能所需的 API Key
- `OPENAI_BASE_URL`：自定义 API 端点

## 📝 维护日志

| 日期 | 内容 |
|------|------|
| 2026-06-01 | 初始化 git 仓库，推送到 GitHub |

## 📄 License

MIT
