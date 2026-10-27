# wecom-cs — WeCom (Enterprise WeChat) Intelligent Customer Service

[中文](#企业微信智能客服系统) | English

Open-source customer service bot for WeCom (企业微信): FAQ exact/fuzzy matching (95%+ hit rate) + LLM fallback, user profiling with conversion-stage tracking (new → converted), sensitive-topic human handoff, hot-reloadable FAQ, Redis-backed session management.

**Why it's different**: dual-engine design keeps factual answers deterministic (FAQ) while the LLM handles only the long tail — support teams keep control of what gets promised. Built and battle-tested in production by [Kunlun Growth](https://kunlungrowth.com), an enterprise AI enablement firm in Hangzhou.

Quick start, architecture and API docs below (Chinese). Contributions welcome.

---

# 企业微信智能客服系统

基于企业微信 API + LLM 大模型的智能客服系统。FAQ 精准匹配 + LLM 智能回复，支持用户画像、转化引导、敏感词转人工。

## 核心特性

- **双引擎回复**：FAQ 精确/模糊匹配（95%+ 命中率）+ LLM 智能生成兜底
- **用户画像**：自动追踪用户互动次数、兴趣话题、转化阶段（new → engaged → interested → considering → converted）
- **人性化回复**：根据用户身份个性化打招呼，FAQ 答案自动加口语化开头和结尾
- **敏感词转人工**：投诉/法律/纠纷等敏感词自动转人工，退款等常规问题 FAQ 自动回复
- **热重载**：FAQ 文件修改后自动生效，无需重启
- **会话管理**：支持 Redis（生产环境）和内存模式（开发环境自动降级）
- **LLM 提示词外置**：品牌话术写在 `prompts/system_prompt.txt`，代码零侵入

## 架构

```
┌─────────────────────────────────────────────────┐
│                 企业微信用户消息                   │
└──────────────────────┬──────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│              FastAPI 回调服务 (main.py)           │
│                                                   │
│  1. 接收消息 → 2. 用户画像 → 3. FAQ匹配           │
│  4. 未命中→LLM → 5. 回复决策 → 6. 人性化处理       │
│  7. 发送回复 → 8. 记录会话                        │
└──────────────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
   ┌──────────┐  ┌──────────┐  ┌───────────┐
   │ FAQ引擎  │  │ LLM引擎  │  │ 会话管理   │
   │faq_engine│  │llm_engine│  │session_mgr│
   └──────────┘  └──────────┘  └───────────┘
         │              │
         ▼              ▼
   ┌──────────┐  ┌──────────┐
   │ faq.json │  │ 回复决策器│
   │(知识库)  │  │reply_ctrl│
   └──────────┘  └──────────┘
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- 企业微信管理员账号
- LLM API Key（推荐 [DeepSeek](https://platform.deepseek.com/)，便宜好用）

### 2. 安装依赖

```bash
cd wecom-cs
pip install -r requirements.txt
```

### 3. 配置

```bash
# 复制配置模板
cp config/config.example.yaml config/config.yaml

# 编辑 config.yaml，填入你的企业微信和 LLM 配置
vim config/config.yaml
```

需要填写的配置项：

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `wecom.corpid` | 企业ID | 企业微信管理后台 → 我的企业 → 企业信息 |
| `wecom.corpsecret` | 应用Secret | 应用管理 → 自建应用 → Secret |
| `wecom.agentid` | 应用AgentId | 应用管理 → 自建应用 → AgentId |
| `wecom.token` | 回调Token | 应用管理 → 自建应用 → 接收消息 → API接收 → Token |
| `wecom.encoding_aes_key` | 加密密钥 | 同上 → EncodingAESKey |
| `llm.api_key` | LLM API Key | DeepSeek/OpenAI 控制台 |
| `llm.api_url` | API地址 | DeepSeek: `https://api.deepseek.com/v1/chat/completions` |
| `llm.model` | 模型名 | DeepSeek: `deepseek-chat` |

### 4. 配置系统提示词

```bash
# 复制提示词模板
cp prompts/system_prompt.example.txt prompts/system_prompt.txt

# 编辑，填入你的品牌信息
vim prompts/system_prompt.txt
```

模板中需要替换的占位符：
- `{品牌名称}` - 你的品牌/产品名
- `{品牌介绍}` - 品牌定位、规模、特色
- `{产品矩阵}` - 产品列表和价格
- `{关键机制}` - 会员体系、积分、活动等

### 5. 配置 FAQ

```bash
# 复制 FAQ 模板
cp knowledge/faq.example.json knowledge/faq.json

# 编辑，添加你的常见问题
vim knowledge/faq.json
```

FAQ 格式说明：

```json
{
  "id": 1,
  "keywords": ["价格", "多少钱", "费用"],
  "question": "价格是多少",
  "answer": "我们的价格是...",
  "confidence": 95
}
```

| 字段 | 说明 |
|------|------|
| `id` | 唯一ID，整数 |
| `keywords` | 关键词列表，用户消息包含任一关键词即匹配 |
| `question` | 问题标准描述 |
| `answer` | 回答内容，建议口语化，不超过300字 |
| `confidence` | 置信度，精确匹配固定为95，模糊匹配系统自动计算 |

### 6. 启动服务

```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 7. 配置回调URL

在企业微信管理后台 → 应用管理 → 自建应用 → 接收消息 → API接收：

- URL: `https://你的域名/wecom/callback`
- Token 和 EncodingAESKey 与 config.yaml 一致

### 8. 测试

在工作台找到你的应用，发送消息测试。或运行测试：

```bash
python3 -m pytest tests/ -v
```

## 项目结构

```
wecom-cs/
├── app/
│   ├── main.py              # FastAPI 主应用，消息处理流程
│   ├── llm_engine.py        # LLM 引擎，加载系统提示词、调用API
│   ├── faq_engine.py        # FAQ 引擎，精确+模糊匹配
│   ├── reply_controller.py  # 回复决策器，敏感词/FAQ/LLM优先级
│   ├── session_manager.py   # 会话管理+用户画像
│   ├── knowledge_base.py    # 知识库管理
│   ├── wecom_client.py      # 企业微信API客户端
│   ├── crypto.py            # 消息加解密
│   └── utils.py             # 工具函数
├── config/
│   ├── config.yaml          # 实际配置（git忽略）
│   └── config.example.yaml  # 配置模板
├── knowledge/
│   ├── faq.json             # 实际FAQ（git忽略）
│   ├── faq.example.json     # FAQ模板
│   └── templates.json       # 回复模板
├── prompts/
│   ├── system_prompt.txt    # 品牌专属提示词（git忽略）
│   └── system_prompt.example.txt  # 提示词模板
├── tests/                   # 测试
├── requirements.txt         # 依赖
├── .gitignore
└── README.md
```

## 自定义指南

### 系统提示词

系统提示词是 LLM 回复的核心，决定了客服的人设、知识范围和说话风格。

编辑 `prompts/system_prompt.txt`，建议包含：
- 品牌介绍（你是谁、做什么的）
- 产品矩阵（有什么产品、价格多少）
- 关键机制（会员体系、活动日历等）
- 说话风格（口语化、简短、不用官方腔）
- 聊天原则（先解决问题、转化引导、不知道就说不知道）

### FAQ 知识库

FAQ 是最快最准的回复方式，建议覆盖：
- 价格/费用（最常问）
- 产品介绍
- 退款/售后政策
- 购买/加入流程
- 营业时间/联系方式
- 常见异议处理

FAQ 支持热重载，修改 `faq.json` 后自动生效。

### 敏感词配置

`reply_controller.py` 中的 `DEFAULT_SENSITIVE_WORDS` 定义了触发转人工的敏感词：
```python
["投诉", "维权", "律师", "报警", "差评", "曝光"]
```

可以根据业务需要增减。注意：常规咨询（如退款）不应放入敏感词，应该用 FAQ 自动回复。

### 用户画像与转化

系统自动追踪用户互动，分5个转化阶段：
- `new` - 新用户，热情但不推销
- `engaged` - 已互动，可以提相关产品
- `interested` - 有兴趣，引导转化
- `considering` - 考虑中，帮助决策
- `converted` - 已转化

转化阶段根据用户消息关键词自动升级（`session_manager.py` 中的 `CONVERSION_KEYWORDS`）。

## 部署

### 生产环境

```bash
# 使用 gunicorn + uvicorn worker
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000

# 或用 systemd 管理进程
# /etc/systemd/system/wecom-cs.service
```

### HTTPS 要求

企业微信回调必须 HTTPS。推荐用 Nginx 反向代理 + Let's Encrypt 证书：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /wecom/callback {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 技术栈

- **后端**：FastAPI + uvicorn
- **LLM**：OpenAI 兼容接口（DeepSeek / OpenAI / 通义千问）
- **存储**：Redis（可选）+ SQLite
- **加解密**：pycryptodome（AES-256-CBC）

## License

MIT
