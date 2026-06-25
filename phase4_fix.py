import os

# ============================================================
# 修改 A: _flush_album_mode 完整重写
# ============================================================
pe_lines = open('module/pyrogram_extension.py', 'r').readlines()

# 找到 _flush_album_mode 的起止行
album_start = None
album_end = None
for i, line in enumerate(pe_lines):
    if 'async def _flush_album_mode(node, items):' in line:
        album_start = i
    if album_start is not None and i > album_start:
        # 找到下一个函数或文件结束
        if line.startswith('async def ') or line.startswith('def '):
            album_end = i - 1
            break
if album_end is None:
    album_end = len(pe_lines) - 1

print(f"_flush_album_mode: lines {album_start+1}-{album_end+1}")

new_album_mode = [
    'async def _flush_album_mode(client, node, items):\n',
    '    """Mode B: 合并媒体项为媒体组(<=10/组)，文字单独转发"""\n',
    '    import pyrogram\n',
    '    captions = [c for c in node.forward_multi_captions if c]\n',
    '    combined = "\\n---\\n".join(captions) if captions else ""\n',
    '\n',
    '    media_items = [m for m in items if m.video or m.photo or m.document]\n',
    '    text_items = [m for m in items if m not in media_items]\n',
    '    temp_files = []\n',
    '\n',
    '    try:\n',
    '        for msg in text_items:\n',
    '            await msg.copy(node.upload_telegram_chat_id,\n',
    '                message_thread_id=node.topic_id, caption="")\n',
    '\n',
    '        for batch_idx in range(0, len(media_items), 10):\n',
    '            batch = media_items[batch_idx:batch_idx + 10]\n',
    '            media_list = []\n',
    '            for j, msg in enumerate(batch):\n',
    '                cap = combined if j == 0 else ""\n',
    '                if msg.video:\n',
    '                    path = await client.download_media(msg.video.file_id)\n',
    '                    temp_files.append(str(path))\n',
    '                    media_list.append(\n',
    '                        pyrogram.types.InputMediaVideo(\n',
    '                            media=str(path), caption=cap))\n',
    '                elif msg.photo:\n',
    '                    media_list.append(\n',
    '                        pyrogram.types.InputMediaPhoto(\n',
    '                            media=msg.photo.file_id,\n',
    '                            caption=cap if j == 0 else ""))\n',
    '                elif msg.document:\n',
    '                    path = await client.download_media(msg.document.file_id)\n',
    '                    temp_files.append(str(path))\n',
    '                    media_list.append(\n',
    '                        pyrogram.types.InputMediaDocument(\n',
    '                            media=str(path), caption=cap))\n',
    '\n',
    '            await node.upload_user.send_media_group(\n',
    '                node.upload_telegram_chat_id, media_list,\n',
    '                message_thread_id=node.topic_id)\n',
    '    finally:\n',
    '        for f in temp_files:\n',
    '            try:\n',
    '                os.remove(f)\n',
    '            except Exception:\n',
    '                pass\n',
    '\n',
]

pe_lines[album_start:album_end + 1] = new_album_mode

# ============================================================
# 修改 B: finalize_forward_multi 传 client
# ============================================================
for i, line in enumerate(pe_lines):
    if 'await _flush_album_mode(node, items)' in line:
        pe_lines[i] = '        await _flush_album_mode(client, node, items)\n'
        print(f"finalize_forward_multi: line {i+1} fixed")
        break

# ============================================================
# 修改 C: except 块加日志 (_flush_single_thumb + _flush_multi_thumb)
# ============================================================
count = 0
for i, line in enumerate(pe_lines):
    if line.strip() == 'except Exception:':
        # 确认在 _flush_single_thumb 或 _flush_multi_thumb 函数内
        ctx = ''.join(pe_lines[max(0,i-30):i])
        if '_flush_single_thumb' in ctx or '_flush_multi_thumb' in ctx:
            indent = ' ' * 8
            pe_lines[i] = indent + 'except Exception as e:\n'
            pe_lines.insert(i+1, indent + '    logger.warning(f"discussion group fallback: {e}")\n')
            count += 1

print(f"except blocks fixed: {count}")

open('module/pyrogram_extension.py', 'w').writelines(pe_lines)

# ============================================================
# 修改 D: send_help_str 欢迎消息
# ============================================================
bot_lines = open('module/bot.py', 'r').readlines()

for i, line in enumerate(bot_lines):
    if 'f"/forward - {_t(\'Forward messages\')}\\n"' in line:
        indent = ' ' * 8
        new_lines = [
            indent + 'f"/forward_screenshot - {_t(\'Forward video to channel with screenshot, video in comments\')}\\n"\n',
            indent + 'f"/forward_multi - {_t(\'Multi video screenshot forward\')}\\n"\n',
            indent + 'f"/forward_album - {_t(\'Merge multiple videos into album\')}\\n"\n',
        ]
        for k, nl in enumerate(new_lines):
            bot_lines.insert(i + 1 + k, nl)
        print(f"send_help_str: lines inserted after line {i+1}")
        break

open('module/bot.py', 'w').writelines(bot_lines)

print("Phase 4 ALL DONE")
