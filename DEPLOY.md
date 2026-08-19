# 企业微信智能客服系统 - 部署指南

## 系统概览

基于企业微信官方 API 的智能客服系统，零封号风险。
- FAQ + LLM 双引擎：常见问题秒回，复杂问题 AI 处理
- 多轮对话上下文记忆
- 敏感消息自动转人工
- 管理后台：FAQ管理、会话查看、统计监控

## 第一步：安装依赖

```bash
cd wecom-cs
pip install -r requirements.txt --break-system-packages
```

依赖列表：FastAPI, uvicorn, httpx, pycryptodome, redis, pyyaml

## 第二步：企业微信后台配置

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」→「自建」→「创建应用」
3. 记下以下信息：
   - **企业ID (corpid)**：在「我的企业」页面
   - **应用 Secret (corpsecret)**：应用详情页
   - **应用 AgentId**：应用详情页
4. 在应用设置中找到「接收消息」→「设置API接收」
5. 配置：
   - **URL**：`https://你的域名/wecom/callback`
   - **Token**：自定义（填入 config.yaml）
   - **EncodingAESKey**：点「随机获取」生成（填入 config.yaml）
6. 先不要点保存（需要服务先启动）

## 第三步：填写配置

编辑 `config/config.yaml`：

```yaml
wecom:
  corpid: "你的企业ID"
  corpsecret: "你的应用Secret"
  agentid: 1000002          # 你的应用AgentId
  token: "你自定义的Token"
  encoding_aes_key: "43位EncodingAESKey"

llm:
  provider: "openai"        # 或 anthropic / qwen
  api_url: "https://api.openai.com/v1/chat/completions"
  api_key: "你的API密钥"
  model: "gpt-4o-mini"     # 或 claude-3-haiku / qwen-turbo
```

## 第四步：启动服务

```bash
./run.sh start
# 或直接运行
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 第五步：验证回调

1. 服务启动后，回到企业微信管理后台
2. 点「保存」配置 API 接收
3. 企业微信会发送验证请求到你的 URL
4. 如果验证通过，说明系统已连通

## 第六步：测试

```bash
# 运行全部测试
./run.sh test

# 用企业微信给应用发消息测试
# 例如发送"课程价格" → 应收到 FAQ 自动回复
# 例如发送"我要投诉" → 应收到转人工提示
```

## 日常运维

```bash
# 查看服务状态
curl http://localhost:8000/health

# 查看统计
curl http://localhost:8000/admin/stats

# 管理FAQ
curl http://localhost:8000/admin/faqs          # 获取所有FAQ
curl -X POST http://localhost:8000/admin/faqs \
  -H "Content-Type: application/json" \
  -d '{"keywords":["新关键词"],"question":"问题","answer":"答案"}'

# 查看用户会话
curl http://localhost:8000/admin/sessions/用户ID
```

## 项目结构

```
wecom-cs/
├── app/
│   ├── main.py              # FastAPI 主应用
│   ├── callback.py           # 回调路由（GET验证+POST接收）
│   ├── crypto.py             # AES加解密+SHA1签名
│   ├── wecom_client.py       # API客户端（token管理+消息发送）
│   ├── faq_engine.py         # FAQ关键词匹配
│   ├── llm_engine.py         # LLM多模型调用
│   ├── knowledge_base.py     # 知识库管理
│   ├── session_manager.py    # Redis会话上下文
│   ├── reply_controller.py   # 回复决策器
│   ├── admin.py              # 管理后台API
│   └── utils.py              # 工具函数
├── config/
│   ├── config.yaml           # 主配置（填入真实密钥）
│   └── config.example.yaml   # 示例配置
├── knowledge/
│   ├── faq.json              # 11条FAQ
│   ├── templates/            # 回复模板
│   └── documents/            # 知识文档
├── tests/                    # 110个测试
├── requirements.txt
├── run.sh
└── DEPLOY.md                 # 本文件
```

## 生产部署建议

1. **HTTPS**：企业微信回调必须 HTTPS，用 nginx 反向代理 + Let's Encrypt
2. **进程管理**：用 systemd 或 supervisor 守护进程
3. **Redis**：安装 Redis 实现多轮对话记忆（不装也能用，降级为内存模式）
4. **LLM**：推荐用通义千问（国内速度快），或 GPT-4o-mini（性价比高）
5. **监控**：定期检查 /admin/stats 接口，关注自动回复率
6. **知识库**：根据实际业务补充 FAQ 和文档到 knowledge/ 目录
