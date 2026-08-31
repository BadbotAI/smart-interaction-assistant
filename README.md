# Smart Interaction Assistant · 智能交互助手

AI 富交互组件 + 多模型智能路由（JiSi）的一体化管理平台 demo。

按《开发文档.md》v0.1 实现的可运行全栈 MVP。三个产品 + 统一数据契约，闭环已打通：
**交互产生标签 → 标签影响路由 → 效果在看板可见**。

## 快速开始

```bash
cd 富交互项目
pip install -r server/requirements.txt   # 仅需 fastapi + uvicorn
python3 -m uvicorn server.app:app --port 8787
```

打开 http://127.0.0.1:8787 。首次启动自动建库（SQLite）并注入种子数据：
6 个 Mock 模型、192 条公共 bank、4 张示例卡片、3 条路由策略、7 天模拟运行历史。

想重置数据：删除 `server/platform.db` 后重启。

## 目录结构

```
contracts/     M0 统一数据契约（先冻结）：事件 Schema、组件协议 Schema、事件字典（指标-字段追溯表）
brand/         多品牌适配的两个包：brand-tokens.*.json（表现层）+ industry-preset.*.json（业务层）
server/        FastAPI 服务端
  db.py            SQLite 数据层
  embeddings.py    确定性文本向量（生产替换为 gte-Qwen2 等，接口不变）
  mockmodels.py    Mock 模型池（隐藏能力画像 + 确定性正确率模拟）
  router_core.py   JiSi 五步 + 快车道 + 探索预算 + 配额 + 双层 bank
  cards.py         卡片状态机 + 发布快照 + 触发匹配 + 调试
  events.py        事件准入清洗 + 标签池 + 租户 bank 回流
  traces.py        Trace/Span 记录与脱敏查询
  dashboard.py     看板聚合（A/B 两类视角 + bank 健康度 + A/B 对比）
  seed.py          种子数据
  app.py           API 入口（含 SSE 流式路由）
web/           前端（原生 JS，无构建步骤）
  components.js    P1 预置组件库（渲染器 + 协议兜底 + 群体决策模式）
  tokens.js        design token 注入（品牌切换不改代码）
  chat.html        对话演示宿主
  cards.html       卡片管理端
  router.html      路由控制台（模型入池 / 策略 / A/B / bank）
  dashboard.html   运营看板
  trace.html       链路追踪
mcp_server/    render/ask/confirm 三工具的 MCP 适配层（对接真实模型时使用）
```

## 演示动线（建议顺序）

1. **对话演示**：点「我的货延误了怎么办」→ 触发客服场景「物流异常处理」→ 先展示管理员配置的信息内容，
   再出交互选项 → 提交后群体回显「XX% 的用户选择了同类答案」→ 选择回传模型继续回答
2. 点「铁矿石近期价格走势如何」→ 观察路由执行过程实时呈现 → 图表组件渲染 →
   双维度反馈（答得准确吗 / 合你的需要吗）→ 聚合路径时出现多回答择优
3. 点「帮我下单采购一批铜精矿」→ 高风险确认闸门
4. 任意回答下点「为什么选它」→ 相似历史问题 + 各模型历史命中率（可解释性）；点「查看链路」→ Trace 瀑布
5. **客服场景模版**：新建场景 → 口语写一句触发条件点「AI 改写」→ 配置信息内容与交互选项
   （或点「AI 匹配交互模版」自动推荐）→ 开群体回显 → 发布；触发调试输入问法验证命中
6. **路由控制台**：注册新模型 → 入池回填任务（带进度/成本预估/可暂停）；调参 → 历史回滚；创建 A/B 实验；
   Bank 页运行 LLM-as-judge 离线批标
7. **运营看板**：场景与交互视角（每个场景的选项占比、参与人数、完成漏斗）+ 模型路由视角（路由分布、
   工具调用情况、成本延迟、标签产出）
8. 回到对话多做几次反馈，观察 bank 健康度中租户层标签增长 → 飞轮闭环

### 客服场景模版（工具1 的产品形态）

类似 Skill 的配置理念：管理员按**场景**配置——

- **触发条件**：一段描述「什么情况下触发」，支持 **AI 改写**（口语描述 → 规范触发条件 + 自动生成示例问法）
- **信息与交互**：触发后先展示配置的信息内容，再按选定的信息模版出交互选项（单选卡片 / 多选 / 打分 /
  登记表 / 方案比选 / 开放问答 / 滑条），模版可由 AI 根据问题自动匹配
- **群体决策回显**：一个开关。开启后用户提交自己的选择时，会看到「XX% 的用户选择了同类答案」与选项分布

## 关键设计决策与文档条目映射

| 文档要求 | 实现位置 |
|---|---|
| §2.2 模型输出零视觉表现 | 组件协议只含语义与数据；样式仅 token（schema 校验 + cards.py 校验非法 token） |
| §2.3.4 feedback.binary 双语义 | 组件双维度（capability/preference），点踩追问原因 |
| §2.4 群体决策为模式开关 | group_mode 配置于卡片；服务端串行化投票；sealed 未揭晓不可见；默认回传 distribution |
| §2.5 触发分工 + 降级链 | 采集/控制卡触发匹配模拟 tool call；呈现型由回答结构化数据映射；渲染失败降级纯文本并报 render_degraded |
| §2.7 版本快照引用 | card_snapshots 不可变快照；Trace 记 card_id+version；旧版本引用提示 + 批量升级 |
| §2.8 异常覆盖 | 幂等提交、字段级校验、API 选项源降级空态、乐观锁冲突对话框、删除卡片另存、本地草稿恢复、空态文案 |
| §3.2 JiSi 五步 | router_core.py，超参全部来自 RoutePolicy，论文默认值起步 |
| §3.3 快车道/等待期填充/档位/超时降级 | 分数分布判据 K=1；SSE 过程事件供 flow.reasoning；极速/均衡/高质三档；超时用已返回模型继续 |
| §3.3 落差四 模型入池 | 带进度、成本预估、可暂停断点续跑的回填任务；覆盖率不足 100% 不得上线 |
| §3.3 落差五 双层 bank | 公共层 tenant_id=NULL 只读；租户层物理隔离；按标签量加权混合（阈值 500 条，TBD-05） |
| §3.4 label_value [0,1] 扩展 | g = label_value × label_confidence × 时间衰减（半衰期 180 天，TBD-07） |
| §3.5 标签管道 | 事件不直写 bank：准入（去重/日限额/AB隔离/preference 不入 capability 计算）→ 标签池 → bank |
| §3.6 探索预算 | explore_ratio 强制随机候选；is_explore 标记；看板监控选择熵 + 坍缩告警 |
| §3.7 配额 | 日预算超限降级单模型；token 四分口径统计 |
| §4.2 A/B 两类视角 | dashboard.html 两个 tab，B 类只有 P1 存在才产得出 |
| §4.5 看板反向控制 | 策略调参/下发/回滚/AB 全在控制台；成本-质量对比表 |
| §4.6 权限与隐私 | Trace 原文默认脱敏；解除脱敏记审计；user_id 假名化存储 |
| §5.1 trace_id 唯一关联键 | 所有事件、决策、标签、span 携带 trace_id |
| §6.1 里程碑 | M0-M6 全部覆盖（M1 组件覆盖呈现型/采集型各约 60%） |

## 与生产环境的差距（刻意保留的演示简化）

- 模型层为 Mock（隐藏能力画像 + 确定性伪随机），替换 `mockmodels.call_model()` 即可接真实 API
- Embedding 为哈希 n-gram 向量，替换 `embeddings.embed()` 即可接真实模型
- 单租户演示（tenant-demo），多租户隔离的数据结构已就位（tenant_id 贯穿全部表）
- 洞察类指标演示环境现算（生产应 T+1 离线聚合）
- LLM-as-judge 为模拟评审（与真实对错约 80% 一致）；生产替换为真实 LLM 评审调用
- 认证/权限模型未实现（TBD-08 待定项）

## 待定事项（按文档 §7 临时默认值实现，代码中以 TBD 编号标注）

TBD-01 聚合延迟验收 25s · TBD-04 上传文件 90 天 · TBD-05 租户 bank 阈值 500 条 ·
TBD-06 Trace 30 热 + 90 冷 · TBD-07 标签半衰期 180 天
