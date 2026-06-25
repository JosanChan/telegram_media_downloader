import os

# 读取当前文件
lines = open('module/pyrogram_extension.py', 'r').readlines()

# 4 个新函数
new_funcs = '''

# === NEW: /forward_multi & /forward_album support ===

async def finalize_forward_multi(client, app, node):
    """总调度: 根据 node 配置分发到对应子模式"""
    items = node.forward_multi_buffer
    if not items:
        return
    if node.forward_multi_mode:
        if node.forward_multi_single_thumb:
            await _flush_single_thumb(client, app, node, items)
        else:
            await _flush_multi_thumb(client, app, node, items)
    elif node.forward_album_mode:
        await _flush_album_mode(node, items)


async def _flush_single_thumb(client, app, node, items):
    """Mode A-单个: 1张缩略图帖 + 全部内容进评论区"""
    first = items[0]
    thumb = None
    if first.video:
        thumb = await download_thumbnail(client, app.temp_save_path, first)

    captions = [c for c in node.forward_multi_captions if c]
    caption = "\\n---\\n".join(captions[:3]) if captions else ""
    caption += f"\\n\\n共{len(items)}个素材"

    if thumb:
        photo_msg = await client.send_photo(
            node.upload_telegram_chat_id, thumb,
            caption=caption, message_thread_id=node.topic_id)
        os.remove(thumb)
    else:
        photo_msg = await client.send_message(
            node.upload_telegram_chat_id, caption,
            message_thread_id=node.topic_id)

    try:
        disc = await client.get_discussion_message(
            node.upload_telegram_chat_id, photo_msg.id)
        for item in items:
            await item.copy(disc.chat.id,
                reply_to_message_id=disc.id,
                message_thread_id=node.topic_id, caption="")
    except Exception:
        for item in items:
            await item.copy(node.upload_telegram_chat_id,
                reply_to_message_id=photo_msg.id,
                message_thread_id=node.topic_id, caption="")


async def _flush_multi_thumb(client, app, node, items):
    """Mode A-多个: 媒体组缩略图相册帖 + 全部内容进评论区"""
    import pyrogram
    thumb_files = []
    media_list = []

    captions = [c for c in node.forward_multi_captions if c]
    combined = "\\n---\\n".join(captions[:3]) if captions else ""
    combined += f"\\n\\n共{len(items)}个素材"

    for i, item in enumerate(items[:10]):
        thumb = None
        if item.video:
            thumb = await download_thumbnail(client, app.temp_save_path, item)
        if thumb:
            thumb_files.append(thumb)
            cap = combined if i == 0 else ""
            media_list.append(
                pyrogram.types.InputMediaPhoto(media=thumb, caption=cap))

    if not media_list:
        photo_msg = await client.send_message(
            node.upload_telegram_chat_id, combined,
            message_thread_id=node.topic_id)
    else:
        msgs = await client.send_media_group(
            node.upload_telegram_chat_id, media_list,
            message_thread_id=node.topic_id)
        photo_msg = msgs[0]

    for f in thumb_files:
        try:
            os.remove(f)
        except Exception:
            pass

    try:
        disc = await client.get_discussion_message(
            node.upload_telegram_chat_id, photo_msg.id)
        for item in items:
            await item.copy(disc.chat.id,
                reply_to_message_id=disc.id,
                message_thread_id=node.topic_id, caption="")
    except Exception:
        for item in items:
            await item.copy(node.upload_telegram_chat_id,
                reply_to_message_id=photo_msg.id,
                message_thread_id=node.topic_id, caption="")


async def _flush_album_mode(node, items):
    """Mode B: 合并媒体项为媒体组(<=10/组)，文字单独转发"""
    import pyrogram
    captions = [c for c in node.forward_multi_captions if c]
    combined = "\\n---\\n".join(captions) if captions else ""

    media_items = [m for m in items if m.video or m.photo or m.document]
    text_items = [m for m in items if m not in media_items]

    for msg in text_items:
        await msg.copy(node.upload_telegram_chat_id,
            message_thread_id=node.topic_id, caption="")

    for batch_idx in range(0, len(media_items), 10):
        batch = media_items[batch_idx:batch_idx + 10]
        media_list = []
        for j, msg in enumerate(batch):
            cap = combined if j == 0 else ""
            if msg.video:
                media_list.append(
                    pyrogram.types.InputMediaVideo(
                        media=msg.video.file_id, caption=cap))
            elif msg.photo:
                media_list.append(
                    pyrogram.types.InputMediaPhoto(
                        media=msg.photo.file_id))
            elif msg.document:
                media_list.append(
                    pyrogram.types.InputMediaDocument(
                        media=msg.document.file_id))
        await node.upload_user.send_media_group(
            node.upload_telegram_chat_id, media_list,
            message_thread_id=node.topic_id)
'''

# 追加到文件末尾
with open('module/pyrogram_extension.py', 'a') as f:
    f.write(new_funcs)

print('Phase 3 DONE')
