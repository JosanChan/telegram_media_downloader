# 一次性恢复 Phase A + 转码 + int 保护的所有改动
content = open('module/pyrogram_extension.py', 'r').read()

# ===== Part 1: _flush_album_mode - replace entire for loop =====
# 找到 for j, msg in enumerate(batch): 到循环结束
# 用带有所有增强功能的新循环替换

old_loop = (
    '            for j, msg in enumerate(batch):\n'
    '                cap = combined if j == 0 else ""\n'
    '                if msg.video:\n'
    '                    media_list.append(\n'
    '                        pyrogram.types.InputMediaVideo(\n'
    '                            media=msg.video.file_id, caption=cap))\n'
    '                elif msg.photo:\n'
    '                    media_list.append(\n'
    '                        pyrogram.types.InputMediaPhoto(\n'
    '                            media=msg.photo.file_id,\n'
    '                            caption=cap if j == 0 else ""))\n'
    '                elif msg.document:\n'
    '                    media_list.append(\n'
    '                        pyrogram.types.InputMediaDocument(\n'
    '                            media=msg.document.file_id, caption=cap))\n'
    '\n'
    '            await node.upload_user.send_media_group(\n'
    '                node.upload_telegram_chat_id, media_list,\n'
    '                message_thread_id=node.topic_id)'
)

new_loop = (
    '            for j, msg in enumerate(batch):\n'
    '                cap = combined if j == 0 else ""\n'
    '                if msg.video:\n'
    '                    try:\n'
    '                        fresh = await client.get_messages(node.chat_id, msg.id)\n'
    '                        if fresh and fresh.video:\n'
    '                            msg = fresh\n'
    '                    except Exception:\n'
    '                        pass\n'
    '                    try:\n'
    '                        path = await asyncio.wait_for(\n'
    '                            client.download_media(msg.video,\n'
    '                                progress=update_upload_stat,\n'
    '                                progress_args=(msg.id, os.path.basename(str(msg.video.file_id)),\n'
    '                                    time.time(), node, client)),\n'
    '                            timeout=300)\n'
    '                        temp_files.append(str(path))\n'
    '                        if _needs_transcode(str(path)):\n'
    '                            logger.info("Transcoding msg %s to H.264...", msg.id)\n'
    '                            fixed = _transcode_video(str(path))\n'
    '                            if fixed:\n'
    '                                temp_files.append(fixed)\n'
    '                                path = fixed\n'
    '                                logger.info("Transcode msg %s complete", msg.id)\n'
    '                        media_list.append(\n'
    '                            pyrogram.types.InputMediaVideo(\n'
    '                                media=str(path), caption=cap,\n'
    '                                width=msg.video.width,\n'
    '                                height=msg.video.height,\n'
    '                                duration=msg.video.duration,\n'
    '                                supports_streaming=True))\n'
    '                        node.stat_forward(ForwardStatus.SuccessForward)\n'
    '                        await report_bot_status(node.bot, node, immediate_reply=True)\n'
    '                    except asyncio.TimeoutError:\n'
    '                        logger.warning("Download timeout for msg %s, skipping", msg.id)\n'
    '                        node.stat_forward(ForwardStatus.FailedForward)\n'
    '                        await report_bot_status(node.bot, node, immediate_reply=True)\n'
    '                        continue\n'
    '                    except Exception as e:\n'
    '                        logger.error("Download failed for msg %s: %s", msg.id, e)\n'
    '                        node.stat_forward(ForwardStatus.FailedForward)\n'
    '                        await report_bot_status(node.bot, node, immediate_reply=True)\n'
    '                        continue\n'
    '                elif msg.photo:\n'
    '                    media_list.append(\n'
    '                        pyrogram.types.InputMediaPhoto(\n'
    '                            media=msg.photo.file_id,\n'
    '                            caption=cap if j == 0 else ""))\n'
    '                    node.stat_forward(ForwardStatus.SuccessForward)\n'
    '                elif msg.document:\n'
    '                    try:\n'
    '                        fresh = await client.get_messages(node.chat_id, msg.id)\n'
    '                        if fresh and fresh.document:\n'
    '                            msg = fresh\n'
    '                    except Exception:\n'
    '                        pass\n'
    '                    try:\n'
    '                        path = await asyncio.wait_for(\n'
    '                            client.download_media(msg.document,\n'
    '                                progress=update_upload_stat,\n'
    '                                progress_args=(msg.id, os.path.basename(str(msg.document.file_id)),\n'
    '                                    time.time(), node, client)),\n'
    '                            timeout=300)\n'
    '                        temp_files.append(str(path))\n'
    '                        media_list.append(\n'
    '                            pyrogram.types.InputMediaDocument(\n'
    '                                media=str(path), caption=cap,\n'
    '                                attributes=msg.document.attributes))\n'
    '                        node.stat_forward(ForwardStatus.SuccessForward)\n'
    '                        await report_bot_status(node.bot, node, immediate_reply=True)\n'
    '                    except asyncio.TimeoutError:\n'
    '                        logger.warning("Download timeout for msg %s, skipping", msg.id)\n'
    '                        node.stat_forward(ForwardStatus.FailedForward)\n'
    '                        await report_bot_status(node.bot, node, immediate_reply=True)\n'
    '                        continue\n'
    '                    except Exception as e:\n'
    '                        logger.error("Download failed for msg %s: %s", msg.id, e)\n'
    '                        node.stat_forward(ForwardStatus.FailedForward)\n'
    '                        await report_bot_status(node.bot, node, immediate_reply=True)\n'
    '                        continue\n'
    '\n'
    '            if media_list:\n'
    '                await node.upload_user.send_media_group(\n'
    '                    node.upload_telegram_chat_id, media_list,\n'
    '                    message_thread_id=node.topic_id)'
)

if old_loop in content:
    content = content.replace(old_loop, new_loop, 1)
    print("Part 1: _flush_album_mode loop replaced with download+progress version")
else:
    print("Part 1: old_loop NOT FOUND - check .bak file")

# ===== Part 2: update_upload_stat - append CloudDriveUploadStat sync =====
old_sync_marker = '\n# pylint: enable=W0201'
sync_code = '''
    if message_id not in node.cloud_drive_upload_stat_dict:
        node.cloud_drive_upload_stat_dict[message_id] = CloudDriveUploadStat(
            file_name=file_name, transferred="", total="",
            percentage="", speed="", eta="")
    cds = node.cloud_drive_upload_stat_dict[message_id]
    total_sz = max(total_size, 1)
    cds.transferred = str(upload_size)
    cds.total = str(total_sz)
    cds.percentage = f"{int(upload_size/total_sz*100)}%"
    cds.speed = f"{max(upload_stat.upload_speed, 0)/1048576:.1f} MB/s"
    if not hasattr(node, "_rpt_tick"):
        node._rpt_tick = 0
    node._rpt_tick += 1
    if node._rpt_tick % 3 == 0:
        await report_bot_status(node.bot, node, immediate_reply=True)
'''
if old_sync_marker in content:
    content = content.replace(old_sync_marker, sync_code + old_sync_marker, 1)
    print("Part 2: CloudDriveUploadStat sync inserted")
else:
    print("Part 2: sync marker NOT FOUND")

# ===== Part 3: _flush_album_mode 开头 - total_forward_task =====
old_temp_files = '    temp_files = []\n'
if old_temp_files in content:
    content = content.replace(old_temp_files,
        '    temp_files = []\n    node.total_forward_task = len(items)\n', 1)
    print("Part 3: total_forward_task inserted")

# ===== Part 4: _flush_album_mode 开头 - initial report_bot_status =====
old_import = '    import pyrogram\n'
if old_import in content:
    content = content.replace(old_import,
        '    import pyrogram\n    await report_bot_status(client, node)\n', 1)
    print("Part 4: initial report_bot_status inserted")

# ===== Part 5: report_bot_status logger.debug -> logger.warning =====
old_debug = '        logger.debug(f"{e}")'
new_debug = '        logger.warning(f"report_bot_status error: {e}")'
if old_debug in content:
    content = content.replace(old_debug, new_debug, 1)
    print("Part 5: logger.debug -> logger.warning")

# ===== Part 6: import subprocess =====
if 'import subprocess' not in content.split('\n')[0:15]:
    content = content.replace('import os\n', 'import os\nimport subprocess\n', 1)
    print("Part 6: import subprocess added")

# ===== Part 7: helper functions at end of file =====
helpers = '''
def _needs_transcode(video_path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10)
        return r.stdout.strip().lower() not in ("h264",)
    except Exception:
        return True

def _transcode_video(video_path):
    out = video_path + "_h264.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-c:v", "libx264",
             "-c:a", "aac", "-movflags", "+faststart", out, "-y"],
            capture_output=True, text=True, timeout=600)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    except Exception:
        pass
    return None
'''
if '_needs_transcode' not in content:
    content += helpers
    print("Part 7: helper functions appended")

open('module/pyrogram_extension.py', 'w').write(content)
print('ALL DONE - restore_phase_a')
