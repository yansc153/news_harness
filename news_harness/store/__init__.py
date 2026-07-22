"""v2 Store 层（ARCHITECTURE.md §6）。

治磁盘 / 缓存 / 释放：db(SQLite 元数据索引) + media(哈希媒体库)
+ cache(响应缓存 TTL/容量) + janitor(配额/TTL/LRU 清理)。
零第三方依赖，仅标准库。
"""
