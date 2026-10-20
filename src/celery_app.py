"""Celery 应用实例，供接口入队与 worker 消费共用。"""
from celery import Celery

celery_app = Celery("bidding")
