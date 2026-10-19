"""資料輸出任務（docs/06 §8）：任務 CRUD 與審批、預覽、執行（csv／jsonl 檔 + manifest + 浮水印）、暫停、執行紀錄、每日例行。"""
from .service import MAX_OUTPUT_ROWS, OutputService
from .transport import OutboxUploader, Uploader

__all__ = ["MAX_OUTPUT_ROWS", "OutboxUploader", "OutputService", "Uploader"]
