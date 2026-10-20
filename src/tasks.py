"""后台任务定义。

接口层只入队并返回 job_id，实际执行在 Celery worker：
- bidding.parse_document：解析 + 结构化抽取 + 建立检索索引
- bidding.write_bid：多角色编写投标文件
"""
from src.celery_app import celery_app
