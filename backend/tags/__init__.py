"""標籤最小版（docs/21 §2）：分群對應值／SQL 兩種建立方式 → dmp.user_tags Bitmap 版本。"""
from .service import Conflict, TagService

__all__ = ["Conflict", "TagService"]
