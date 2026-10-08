# -*- coding: utf-8 -*-
"""作业实现：B 站大语言模型相关视频弹幕分析.

包结构：
    config          全局配置
    bili_client     HTTP 客户端（限速/重试/wbi 签名）
    video_search    视频检索（综合排序，取前 N）
    danmaku_crawler 弹幕抓取（断点续爬）
    filters         噪声正则与命中判断
    cleaner         弹幕清洗
    tokenizer       中文分词
    analyzer        统计（每类总量、Top-N 弹幕、词频）
    exporter        Excel 导出
    visualizer      词云与图表
    dashboard       pyecharts 可视化大屏
    profile_runner  性能分析
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
