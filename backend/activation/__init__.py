"""受眾名單推送：目的地、推送單、Webhook 交付（docs/14）。"""
from .service import ActivationService, Conflict, customer_code, httpx_transport

__all__ = ["ActivationService", "Conflict", "customer_code", "httpx_transport"]
