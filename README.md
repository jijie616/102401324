# 2025软工K班个人编程任务：B站大语言模型相关视频弹幕分析挖掘

> 学号：**102401324**
> 选题：大语言模型应用相关视频弹幕分析挖掘
> 语言/技术栈：Python 3.10+ / requests / jieba / pandas / matplotlib / wordcloud / pyecharts

## 一、这个项目做什么

1. **爬**：用 B 站官方 Web 接口，按「大语言模型」「大模型」「LLM」三个关键词检索**综合排序前 300** 的相关视频，
   再逐个抓取其弹幕（XML 接口为主，6 分钟分段的 Protobuf 接口为补充）。
2. **洗**：过滤「666」「哈哈哈」「前排打卡」等噪声弹幕，去重、标准化。
3. **算**：统计每类弹幕总量、数量排名前 8 的弹幕、高频词、应用领域分布、
   用户对成本/风险/收益的关注度，以及弹幕在视频时间轴上的分布。
4. **出**：结果自动写入多 Sheet 的 `output/danmaku_analysis.xlsx`。
5. **画**：生成词云图、Top8 弹幕柱状图、领域分布图、情感饼图、时间轴折线图，
   以及一个 pyecharts 单文件**可视化大屏** `output/dashboard.html`。
6. **析**：基于统计数据给出「B站用户对 LLM 的主流看法」结论（见 `docs/结论.md`）。

## 二、目录结构

```
102401324/
├── src/llm_danmaku/          # 业务代码（按职责分层，低耦合）
│   ├── config.py             # 全局配置（路径、关键词、阈值、字体）
│   ├── bili_client.py        # HTTP 客户端：限速、重试、wbi 签名、登录态
│   ├── video_search.py       # 视频检索：多关键词分页 + 去重 + 取前 N
│   ├── danmaku_crawler.py    # 弹幕抓取：断点续爬、失败隔离、XML/Protobuf 双通道
│   ├── filters.py            # 噪声规则（正则 + 黑名单）
│   ├── cleaner.py            # 清洗流水线 + 数据质量报告
│   ├── tokenizer.py          # jieba 分词 + 领域词典 + 停用词
│   ├── analyzer.py           # 统计：Top-N、词频、领域、情感、时间分布
│   ├── exporter.py           # 导出多 Sheet xlsx
│   ├── visualizer.py         # 词云与 matplotlib 图表
│   ├── dashboard.py          # pyecharts 可视化大屏
│   ├── profile_runner.py     # cProfile 性能分析（含分析图）
│   └── pipeline.py           # CLI 流水线编排（分阶段可单独执行）
├── tests/                    # 单元测试（pytest，34+ 用例）
├── resources/                # 停用词等静态资源
├── docs/                     # PSP 表、结论、性能分析报告
├── data/                     # 运行产物（视频清单入库，原始弹幕不入库）
├── output/                   # xlsx / 词云 / 图表 / 大屏 HTML（不入库）
├── requirements.txt
├── pytest.ini
└── README.md
```

## 三、快速开始

```bash
# 1. 安装依赖（国内建议先配镜像：pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/）
pip install -r requirements.txt

# 2. 全流程一键运行
python -m llm_danmaku.pipeline

# 3. 小样验证（先抓 20 个视频看看效果）
python -m llm_danmaku.pipeline --limit 20

# 4. 分阶段执行（代码有进展即签入时，每步单独验证）
python -m llm_danmaku.pipeline --stage search      # 只检索视频
python -m llm_danmaku.pipeline --stage crawl       # 只抓弹幕（自动断点续爬）
python -m llm_danmaku.pipeline --stages clean,analyze,export,visualize,dashboard
```

> 未登录时部分视频只返回部分弹幕。如需更完整数据，在环境变量里设置
> `BILI_SESSDATA`（浏览器 F12 → Application → Cookies 中复制 SESSDATA 的值）。

## 四、运行产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 视频清单 | `data/videos.json` | 综合排序前 300 的视频元信息 |
| 原始弹幕缓存 | `data/raw/<BV号>.jsonl` | 断点续爬用，删除后重跑会重新抓取 |
| 清洗后弹幕 | `data/danmaku_cleaned.jsonl` | 全量清洗结果 |
| 统计结果 | `output/danmaku_stats.json` | 可视化的唯一数据来源 |
| **Excel 报表** | `output/danmaku_analysis.xlsx` | 9 个 Sheet：总览/Top弹幕/词频/视频清单/领域/情感… |
| **词云图** | `output/wordcloud.png` | 作业 2.3 核心要求 |
| 图表 | `output/charts/*.png` | Top 弹幕、高频词、领域、情感、时间分布 |
| **可视化大屏** | `output/dashboard.html` | 附加题，双击浏览器打开 |
| 性能分析 | `output/profile/profile_chart.png` | 作业 3.3 要求贴的性能分析图 |

## 五、测试与覆盖率

```bash
python -m pytest                                   # 运行全部单元测试
python -m pytest --cov=src/llm_danmaku --cov-report=term-missing   # 带覆盖率
python -m pytest --cov=src/llm_danmaku --cov-report=html           # 生成 htmlcov/index.html
```

## 六、性能分析

```bash
python -m llm_danmaku.profile_runner                  # 用真实数据
python -m llm_danmaku.profile_runner --synthetic 50000  # 合成数据压测
```

产物在 `output/profile/`：`profile_stats.txt`（文本报告）、`profile_stats.prof`（可用
`snakeviz` 交互查看）、`profile_chart.png`（可直接贴进博客的性能分析图）。

## 七、技术要点（对应作业评分点）

| 评分点 | 本项目的落实方式 |
|---|---|
| 3.2 爬虫与数据处理 | wbi 签名（含匿名可取 wbi_img 的踩坑处理）；XML + Protobuf 双通道弹幕抓取；视频内去重跨视频计数的统计口径；断点续爬与失败隔离 |
| 3.3 性能改进 | cProfile 定位瓶颈 → jieba 词典 `lru_cache` 只加载一次、正则预编译、Counter 单次遍历多路统计；**爬虫并发吞吐 0.5 → 2.3+ 请求/秒** |
| 3.4 结论可靠性 | 每条结论给出"数据来源 + 判断方式 + 置信度 + 已知局限"，见 `docs/结论.md` |
| 3.5 可视化 | 词云 + 6 张静态图 + pyecharts 单文件大屏（9 个可视化组件） |
| 5.1 结构完整性 | 14 个分层模块、统一异常处理、**107 个单元测试**、9 个 Sheet 的 xlsx |
| 5.2/5.3 可读性与命名 | 全量中文 docstring（含 Args/Returns/Examples）、类型注解、snake_case/PascalCase 规范 |
| 6.1 附加题一 | 8 家国内外科技媒体观点 + arXiv 12 个月 2400 篇论文的趋势动量分析 |
| 6.2 附加题二 | 可视化大屏、数据质量报告、双通道自适应抓取、全局令牌桶 + 熔断退避 |

## 八、工程健壮性设计

长时间爬取会遇到网络抖动与目标站风控，本项目为此设计了六层保护：

| 机制 | 说明 |
|---|---|
| 断点续爬 | 每个视频落盘独立 JSONL（`.tmp` + 原子替换写入），重跑自动跳过已完成 |
| 失败隔离 | 单视频失败只记入 `failed.log`，不影响其余视频 |
| 数据保护 | 重爬时若某视频未抓到弹幕而本地已有非空缓存，则保留原缓存，避免覆盖丢失 |
| 指数退避 | 失败重试 `1.5^attempt`；遇 412 风控额外加倍退避 |
| 全局令牌桶 | 跨线程共享最小请求间隔（0.4s），并发调高也不突破总速率上限 |
| 熔断保护 | 连续失败 8 次进入冷却，冷却时长按 2 倍递增（60→120→…→900s 封顶） |
| 单视频时间预算 | 45 秒，超时立即保存已抓部分并进入下一个视频 |

> 这些不是过度设计：实测中**确实**遇到过一次突发限流（短时间内数千请求后
> 弹幕接口持续返回 412），熔断 + 长退避是让任务最终能跑完的关键。

## 九、免责声明

本项目仅用于课程学习与学术研究，爬取过程已做全局限速、指数退避与熔断保护，
数据仅用于统计分析，不存储用户隐私信息，不用于任何商业用途。
