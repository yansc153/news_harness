"""v2 connector 包。

分层：
- connectors/source/      金融平台抓取（Reddit / 雪球 ...）
- connectors/processing/  翻译 + LLM 处理链（translate / llm）
- connectors/base.py      抽象基类与异常
- connectors/registry.py  类注册表（配置驱动发现的基础）
"""
