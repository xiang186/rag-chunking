"""
实时日志服务 - WebSocket 广播日志消息。

设计：
1. LogEmitter 全局单例，线程安全地收集和广播日志
2. 每个 WebSocket 连接获得一个异步队列，收到新日志时推入所有队列
3. 服务层通过 emit_log() 发送结构化日志，无需关心传输细节
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional


class LogLevel(str, Enum):
    """日志级别。"""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SUCCESS = "success"


@dataclass
class LogEntry:
    """
    单条日志记录。

    序列化为 JSON 后发送给前端 WebSocket 客户端。
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: float = field(default_factory=time.time)
    level: str = LogLevel.INFO
    message: str = ""
    source: str = ""  # 来源模块，如 "chunk_service"、"upload"
    duration_ms: Optional[int] = None  # 操作耗时（毫秒）
    extra: Dict[str, Any] = field(default_factory=dict)  # 附加数据

    def to_dict(self) -> Dict[str, Any]:
        """序列化为前端可用的字典。"""
        result = {
            "id": self.id,
            "timestamp": self.timestamp,
            "level": self.level if isinstance(self.level, str) else self.level.value,
            "message": self.message,
            "source": self.source,
        }
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        if self.extra:
            result["extra"] = self.extra
        return result


class LogEmitter:
    """
    全局日志发射器（单例）。

    工作方式：
    - 服务层调用 emit_log() 将日志推入所有活跃的 WebSocket 队列
    - WebSocket 端点调用 subscribe() 获得一个 AsyncGenerator，逐条 yield 日志
    - 线程安全：使用 asyncio.Lock 保护队列操作
    """

    def __init__(self, max_history: int = 500):
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._history: List[LogEntry] = []
        self._max_history = max_history
        self._lock = asyncio.Lock()

    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        订阅日志流，返回异步生成器。

        前端 WebSocket 连接调用此方法后，新日志会自动推送到客户端。
        连接断开时自动清理队列。
        """
        sub_id = uuid.uuid4().hex[:8]
        queue: asyncio.Queue = asyncio.Queue()

        async with self._lock:
            self._subscribers[sub_id] = queue
            # 先推送历史日志
            for entry in self._history:
                await queue.put(entry.to_dict())

        try:
            while True:
                data = await queue.get()
                yield data
        except asyncio.CancelledError:
            pass
        finally:
            async with self._lock:
                self._subscribers.pop(sub_id, None)

    async def emit(
        self,
        message: str,
        level: str | LogLevel = LogLevel.INFO,
        source: str = "",
        duration_ms: int | None = None,
        **extra: Any,
    ) -> None:
        """
        发送一条日志，推送给所有订阅者。

        Args:
            message: 日志内容
            level: 日志级别
            source: 来源模块标识
            duration_ms: 操作耗时
            **extra: 附加字段
        """
        level_str = level if isinstance(level, str) else level.value
        entry = LogEntry(
            level=level_str,
            message=message,
            source=source,
            duration_ms=duration_ms,
            extra=extra,
        )

        # 保存历史
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # 广播给所有订阅者
        data = entry.to_dict()
        async with self._lock:
            dead_subs = []
            for sub_id, queue in self._subscribers.items():
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    # 队列满说明客户端消费太慢，丢弃最旧的日志腾出空间
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        dead_subs.append(sub_id)
            # 清理无法恢复的订阅
            for sub_id in dead_subs:
                self._subscribers.pop(sub_id, None)

    def emit_sync(
        self,
        message: str,
        level: str | LogLevel = LogLevel.INFO,
        source: str = "",
        duration_ms: int | None = None,
        **extra: Any,
    ) -> None:
        """
        同步版本的 emit，用于在同步上下文中调用。

        将日志放入历史记录，但不推送到 WebSocket（因为同步上下文无法 await）。
        如果事件循环正在运行，会通过 call_soon_threadsafe 尝试异步推送。
        """
        level_str = level if isinstance(level, str) else level.value
        entry = LogEntry(
            level=level_str,
            message=message,
            source=source,
            duration_ms=duration_ms,
            extra=extra,
        )

        # 保存历史
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # 尝试在事件循环中推送
        data = entry.to_dict()
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._async_push, data)
        except RuntimeError:
            # 没有运行中的事件循环，日志仅存入历史
            pass

    def _async_push(self, data: Dict[str, Any]) -> None:
        """在事件循环中推送日志到订阅者队列。"""
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass

    @property
    def subscriber_count(self) -> int:
        """当前活跃的订阅者数量。"""
        return len(self._subscribers)

    async def clear_history(self) -> None:
        """清除历史日志。"""
        self._history.clear()


# 全局单例
log_emitter = LogEmitter()


# ── 便捷函数 ──

async def emit_log(
    message: str,
    level: str | LogLevel = LogLevel.INFO,
    source: str = "",
    duration_ms: int | None = None,
    **extra: Any,
) -> None:
    """异步发送日志（推荐在 async 函数中使用）。"""
    await log_emitter.emit(message, level, source, duration_ms, **extra)


def emit_log_sync(
    message: str,
    level: str | LogLevel = LogLevel.INFO,
    source: str = "",
    duration_ms: int | None = None,
    **extra: Any,
) -> None:
    """同步发送日志（在同步函数中使用，仅存入历史，尽力推送）。"""
    log_emitter.emit_sync(message, level, source, duration_ms, **extra)
