content = open('module/pyrogram_extension.py', 'r').read()

# === Fix 1: import subprocess ===
content = content.replace('import os\n', 'import os\nimport subprocess\n', 1)

# === Fix 2: percentage format ===
content = content.replace(
    'cds.percentage = f"{upload_size/total_sz*100:.1f}"',
    'cds.percentage = f"{int(upload_size/total_sz*100)}%"'
)

# === Fix 3: delete duplicate total_forward_task ===
old_dup = '    node.total_forward_task = len(items)\n    node.total_forward_task = len(items)\n'
if old_dup in content:
    content = content.replace(old_dup, '    node.total_forward_task = len(items)\n', 1)

# === Fix 4: append helper functions ===
helpers = '''
def _needs_transcode(video_path):
    """Detect if video needs H.264 transcoding via ffprobe"""
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
    """Transcode video to H.264 + AAC via ffmpeg"""
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
content += helpers

# === Fix 5: insert transcoding after video/document download ===
old_vid = (
    '                        temp_files.append(str(path))\n'
    '                        media_list.append(\n'
    '                            pyrogram.types.InputMediaVideo('
)
insert = (
    '                        if _needs_transcode(str(path)):\n'
    '                            logger.info("Transcoding msg %s to H.264...", msg.id)\n'
    '                            fixed = _transcode_video(str(path))\n'
    '                            if fixed:\n'
    '                                temp_files.append(fixed)\n'
    '                                path = fixed\n'
    '                                logger.info("Transcode msg %s complete", msg.id)\n'
)
if old_vid in content:
    content = content.replace(old_vid, insert + old_vid, 2)

open('module/pyrogram_extension.py', 'w').write(content)
print('ALL DONE')
