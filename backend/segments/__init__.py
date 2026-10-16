"""分群引擎最小版：規則 = UsersQuery → Bitmap → dmp.segment_members 版本表；狀態機、審批、例行排程、版本保留、回收站、血緣。"""
from .engine import Conflict, SegmentService

__all__ = ["Conflict", "SegmentService"]
