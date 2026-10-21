"""Celery 应用实例，供接口入队与 worker 消费共用。"""
from celery import Celery

from src.config import settings

celery_app = Celery(
    "bidding",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["src.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    task_default_queue="bidding",
    worker_pool=settings.celery_worker_pool,
    result_expires=settings.celery_result_expires,
    task_always_eager=settings.celery_task_always_eager,
)
