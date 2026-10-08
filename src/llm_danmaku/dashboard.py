# -*- coding: utf-8 -*-
"""可视化大屏：用 pyecharts 生成单文件 HTML（附加题 6.2「自主发挥」）。

选 pyecharts 而不是 Streamlit 的原因：产物是**单个 HTML 文件**，
双击即可打开、可直接放进仓库、也能截图进博客，无运行环境依赖。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .analyzer import DanmakuStats

logger = logging.getLogger(__name__)


def _page(title: str):
    """创建带深色主题的 Page 容器。"""
    from pyecharts.charts import Page

    page = Page(layout=Page.DraggablePageLayout, page_title=title)
    return page


def build_dashboard(stats: DanmakuStats, path: Path = config.DASHBOARD_FILE) -> Optional[Path]:
    """生成可视化大屏 HTML。

    Args:
        stats: 统计结果。
        path: 输出 HTML 路径。

    Returns:
        成功返回路径；pyecharts 缺失时返回 None（不影响主流程）。
    """
    try:
        from pyecharts import options as opts
        from pyecharts.charts import Bar, Line, Pie, WordCloud
        from pyecharts.commons.utils import JsCode
    except ImportError:
        logger.warning("未安装 pyecharts，跳过大屏生成（pip install pyecharts）")
        return None

    charts: List[object] = []

    # ---------- 1. 词云 ----------
    if stats.top_words:
        wc = (
            WordCloud(init_opts=opts.InitOpts(theme="dark", width="100%", height="420px"))
            .add(
                "",
                [list(item) for item in stats.top_words[:200]],
                word_size_range=[12, 70],
                shape="circle",
                textstyle_opts=opts.TextStyleOpts(font_family="Microsoft YaHei"),
            )
            .set_global_opts(
                title_opts=opts.TitleOpts(
                    title="弹幕高频词云", subtitle="数据来源：B站 LLM 相关视频弹幕"),
                tooltip_opts=opts.TooltipOpts(is_show=True),
            )
        )
        charts.append(wc)

    # ---------- 2. Top 弹幕 ----------
    if stats.top_comments:
        items = stats.top_comments[: config.TOP_COMMENT_N]
        bar = (
            Bar(init_opts=opts.InitOpts(theme="dark", width="100%", height="420px"))
            .add_xaxis([t if len(t) <= 12 else t[:11] + "…" for t, _ in items])
            .add_yaxis(
                "出现次数",
                [c for _, c in items],
                itemstyle_opts=opts.ItemStyleOpts(
                    color=JsCode(
                        "new echarts.graphic.LinearGradient(0,0,0,1,["
                        "{offset:0,color:'#EE5A6F'},{offset:1,color:'#F8B195'}])"
                    )
                ),
                label_opts=opts.LabelOpts(position="top"),
            )
            .reversal_axis()
            .set_global_opts(
                title_opts=opts.TitleOpts(title=f"数量排名前 {len(items)} 的弹幕"),
                legend_opts=opts.LegendOpts(is_show=False),
                xaxis_opts=opts.AxisOpts(name="出现次数"),
            )
        )
        charts.append(bar)

    # ---------- 3. 应用领域 ----------
    domain_items = stats.domain_top(10)
    if domain_items:
        pie = (
            Pie(init_opts=opts.InitOpts(theme="dark", width="100%", height="420px"))
            .add(
                "",
                [list(item) for item in domain_items],
                radius=["35%", "68%"],
                rosetype="radius",
                label_opts=opts.LabelOpts(formatter="{b}: {d}%"),
            )
            .set_global_opts(
                title_opts=opts.TitleOpts(title="大语言模型应用领域分布"),
                legend_opts=opts.LegendOpts(orient="vertical", pos_top="15%", pos_left="2%"),
            )
        )
        charts.append(pie)

    # ---------- 4. 态度分布 ----------
    sentiment_pairs = [("正面", stats.positive_count), ("负面", stats.negative_count),
                       ("中性", stats.neutral_count)]
    if sum(v for _, v in sentiment_pairs) > 0:
        pie2 = (
            Pie(init_opts=opts.InitOpts(theme="dark", width="100%", height="420px"))
            .add(
                "",
                [[k, v] for k, v in sentiment_pairs],
                radius=["40%", "70%"],
                label_opts=opts.LabelOpts(formatter="{b}: {d}%"),
            )
            .set_global_opts(
                title_opts=opts.TitleOpts(title="用户态度倾向"),
                legend_opts=opts.LegendOpts(is_show=False),
            )
        )
        charts.append(pie2)

    # ---------- 5. 关注点对比 ----------
    concern_pairs = [("成本价格", stats.cost_count), ("风险隐患", stats.risk_count),
                     ("效率收益", stats.benefit_count)]
    if sum(v for _, v in concern_pairs) > 0:
        bar2 = (
            Bar(init_opts=opts.InitOpts(theme="dark", width="100%", height="420px"))
            .add_xaxis([k for k, _ in concern_pairs])
            .add_yaxis("弹幕数", [v for _, v in concern_pairs],
                       label_opts=opts.LabelOpts(position="top"))
            .set_global_opts(
                title_opts=opts.TitleOpts(title="用户关注点分布"),
                legend_opts=opts.LegendOpts(is_show=False),
            )
        )
        charts.append(bar2)

    # ---------- 6. 时间轴分布 ----------
    if stats.progress_buckets:
        line = (
            Line(init_opts=opts.InitOpts(theme="dark", width="100%", height="420px"))
            .add_xaxis([k for k, _ in stats.progress_buckets])
            .add_yaxis(
                "弹幕数", [v for _, v in stats.progress_buckets],
                is_smooth=True, is_symbol_show=True,
                areastyle_opts=opts.AreaStyleOpts(opacity=0.3),
            )
            .set_global_opts(
                title_opts=opts.TitleOpts(title="弹幕在视频时间轴上的分布"),
                xaxis_opts=opts.AxisOpts(name="视频进度"),
                yaxis_opts=opts.AxisOpts(name="弹幕数"),
            )
        )
        charts.append(line)

    if not charts:
        logger.warning("没有可展示的图表，跳过大屏生成")
        return None

    # ---------- 7. 顶部关键指标（用 Page 的 HTML 头部承载） ----------
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    page = _page("B站大语言模型弹幕分析大屏")
    page.add(*charts)
    page.render(str(out))

    _inject_kpi_header(out, stats)
    logger.info("可视化大屏已生成：%s", out)
    return out


def _inject_kpi_header(html_path: Path, stats: DanmakuStats) -> None:
    """往 pyecharts 生成的 HTML 中注入一行关键指标卡片。"""
    kpis: Sequence[Tuple[str, str]] = (
        ("抓取视频", f"{stats.video_count}"),
        ("原始弹幕", f"{stats.total_raw:,}"),
        ("有效弹幕", f"{stats.total_cleaned:,}"),
        ("唯一弹幕", f"{stats.total_unique:,}"),
        ("覆盖领域", f"{len(stats.domain_counts)}"),
        ("正面占比", f"{stats.positive_ratio * 100:.1f}%"),
    )
    cards = "".join(
        f'<div class="kpi-card"><div class="kpi-value">{value}</div>'
        f'<div class="kpi-label">{label}</div></div>'
        for label, value in kpis
    )
    style = """
<style>
  body { background: #0f1420 !important; }
  .kpi-bar { display:flex; flex-wrap:wrap; gap:16px; justify-content:center;
             padding:22px 16px 6px; background:#0f1420; }
  .kpi-card { min-width:150px; padding:16px 22px; border-radius:14px;
              background:linear-gradient(145deg,#1b2436,#131a28);
              border:1px solid #2b3a55; text-align:center;
              box-shadow:0 6px 18px rgba(0,0,0,.35); }
  .kpi-value { font-size:30px; font-weight:700; color:#38d9ff; line-height:1.2;
               font-family:Consolas,Menlo,monospace; }
  .kpi-label { font-size:13px; color:#9fb3c8; margin-top:6px; letter-spacing:1px; }
  h1, .chart-container { background:transparent !important; }
</style>
"""
    try:
        html = html_path.read_text(encoding="utf-8")
        header = f'{style}<div class="kpi-bar">{cards}</div>'
        if "</body>" in html:
            html = html.replace("</body>", header + "</body>", 1)
        else:
            html += header
        html_path.write_text(html, encoding="utf-8")
    except OSError as exc:
        logger.warning("大屏指标头部注入失败：%s", exc)
