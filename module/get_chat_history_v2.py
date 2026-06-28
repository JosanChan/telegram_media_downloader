"""Rewrite pyrogram.get_chat_history"""

from datetime import datetime
from typing import AsyncGenerator, Optional, Union

import pyrogram

# pylint: disable = W0611
from pyrogram import raw, types, utils


async def get_chunk_v2(
    *,
    client: pyrogram.Client,
    chat_id: Union[int, str],
    limit: int = 0,
    offset: int = 0,
    max_id: int = 0,
    from_message_id: int = 0,
    from_date: datetime = utils.zero_datetime(),
    reverse: bool = False
):
    """get chunk"""
    from_message_id = from_message_id or (1 if reverse else 0)

    messages = await utils.parse_messages(
        client,
        await client.invoke(
            raw.functions.messages.GetHistory(
                peer=await client.resolve_peer(chat_id),
                offset_id=from_message_id,
                offset_date=utils.datetime_to_timestamp(from_date),
                add_offset=offset * (-1 if reverse else 1) - ((limit - 1) if reverse else 0),
                limit=limit,
                max_id=max_id,
                min_id=0,
                hash=0,
            ),
            sleep_threshold=60,
        ),
        replies=0,
    )

    if reverse:
        messages.reverse()

    return messages


# pylint: disable = C0301
async def get_chat_history_v2(
    self: pyrogram.Client,
    chat_id: Union[int, str],
    limit: int = 0,
    max_id: int = 0,
    offset: int = 0,
    offset_id: int = 0,
    offset_date: datetime = utils.zero_datetime(),
    reverse: bool = False,
) -> Optional[AsyncGenerator["types.Message", None]]:
    """Get messages from a chat history."""
    current = 0
    total = limit or (1 << 31) - 1
    limit = min(100, total)

    # reverse 模式的下界（首个请求起点），用于过滤空洞导致的越界消息
    start_id = offset_id

    while True:
        request_from_id = offset_id
        messages = await get_chunk_v2(
            client=self,
            chat_id=chat_id,
            limit=limit,
            offset=offset,
            max_id=max_id + 1 if max_id else 0,
            from_message_id=offset_id,
            from_date=offset_date,
            reverse=reverse,
        )

        if not messages:
            return

        # 防重复/防死循环：reverse 模式下若本轮返回的最大 id 没有越过请求起点，
        # 说明已到达范围/频道末尾——GetHistory 因 add_offset 被服务端 clamp，
        # 重复返回了最新的旧窗口。此时必须停止，否则会反复转发最后一批消息
        # （total 按 ID 跨度估算偏大，无法及时通过 current 终止）。
        if reverse and request_from_id and messages[-1].id < request_from_id:
            return

        offset_id = messages[-1].id + (1 if reverse else 0)

        for message in messages:
            # 范围过滤：丢弃越界消息（offset_id 落在空洞时锚点会漂移并泄漏边界外消息）
            if reverse and (
                (start_id and message.id < start_id)
                or (max_id and message.id > max_id)
            ):
                continue

            yield message

            current += 1

            if current >= total:
                return

            # 按终止 ID 停止：reverse 模式一旦取到上界消息即结束，不再依赖凑够
            # total 数量——源频道中间消息被删除时真实消息数 < ID 跨度，total 永远
            # 凑不齐，会卡在末尾反复返回最后一条（clamp）。
            if reverse and max_id and message.id >= max_id:
                return
