"""实时日志 WebSocket API 路由。"""

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.log_service import log_emitter

router = APIRouter(tags=["logs"])


@router.websocket("/api/logs/ws")
async def logs_websocket(websocket: WebSocket):
    """
    WebSocket 端点 - 实时推送日志消息。

    协议：
    - 连接后立即推送历史日志
    - 之后每条新日志实时推送
    - 消息格式：JSON，包含 id / timestamp / level / message / source / duration_ms / extra

    客户端可发送以下控制消息：
    - {"action": "clear"}  清除服务端历史日志
    - {"action": "ping"}   心跳，服务端回复 {"type": "pong"}
    """
    await websocket.accept()
    subscriber = log_emitter.subscribe()

    try:
        # 并发：推送日志 + 接收客户端消息
        async def receive_loop():
            """接收客户端控制消息。"""
            try:
                while True:
                    raw = await websocket.receive_text()
                    try:
                        msg = json.loads(raw)
                        action = msg.get("action")
                        if action == "clear":
                            await log_emitter.clear_history()
                            await websocket.send_json({"type": "cleared"})
                        elif action == "ping":
                            await websocket.send_json({"type": "pong"})
                    except json.JSONDecodeError:
                        pass
            except WebSocketDisconnect:
                pass

        async def send_loop():
            """推送日志到客户端。"""
            try:
                async for log_data in subscriber:
                    await websocket.send_json(log_data)
            except WebSocketDisconnect:
                pass

        # 同时运行收发循环
        recv_task = asyncio.create_task(receive_loop())
        send_task = asyncio.create_task(send_loop())

        # 任一循环结束则关闭
        done, pending = await asyncio.wait(
            [recv_task, send_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@router.get("/api/logs/history")
async def get_log_history(limit: int = Query(default=100, ge=1, le=1000)):
    """获取历史日志（REST API 备用，用于 WebSocket 不可用时）。"""
    history = log_emitter._history[-limit:]
    return {"logs": [entry.to_dict() for entry in history], "total": len(history)}
