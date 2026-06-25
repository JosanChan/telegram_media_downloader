content = open('module/pyrogram_extension.py', 'r').read()

# ===== Fix 1: percentage int() in cloud_drive_upload_stat block =====
# Move try/except BEFORE the f-string block, use pct inside
old1 = (
    "            temp_file_name = truncate_filename(os.path.basename(value.file_name), 10)\n"
    "            upload_msg_detail_str += (\n"
    "                f\" ├─ 🆔 {_t('Message ID')}: {idx}\\n\"\n"
    "                f\" │   ├─ 📁 : {temp_file_name}\\n\"\n"
    "                f\" │   ├─ 📏 : {value.total}\\n\"\n"
    "                f\" │   ├─ ⏫ : {value.speed}\\n\"\n"
    "                f\" │   └─ 📊 : [\"\n"
    "                f'{create_progress_bar(int(value.percentage.split(\"%\")[0]))}]'\n"
    "                f\" ({value.percentage})%\\n\"\n"
    "            )"
)

new1 = (
    "            try:\n"
    "                pct = int(value.percentage.split(\"%\")[0])\n"
    "            except (ValueError, IndexError):\n"
    "                pct = 0\n"
    "            temp_file_name = truncate_filename(os.path.basename(value.file_name), 10)\n"
    "            upload_msg_detail_str += (\n"
    "                f\" ├─ 🆔 {_t('Message ID')}: {idx}\\n\"\n"
    "                f\" │   ├─ 📁 : {temp_file_name}\\n\"\n"
    "                f\" │   ├─ 📏 : {value.total}\\n\"\n"
    "                f\" │   ├─ ⏫ : {value.speed}\\n\"\n"
    "                f\" │   └─ 📊 : [\"\n"
    "                f'{create_progress_bar(pct)}]'\n"
    "                f\" ({value.percentage})%\\n\"\n"
    "            )"
)

if old1 in content:
    content = content.replace(old1, new1, 1)
    print("Fix 1: cloud_drive percentage int() protection applied")
else:
    print("Fix 1: pattern NOT FOUND - check indentation")

# ===== Fix 2: download progress int() =====
old2 = '                progress = int(value["down_byte"] / max(value["total_size"], 1) * 100)'
new2 = '                try:\n                    progress = int(value["down_byte"] / max(value["total_size"], 1) * 100)\n                except ValueError:\n                    progress = 0'
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("Fix 2: download progress int() protection applied")
else:
    print("Fix 2: pattern NOT FOUND")

# ===== Fix 3: upload progress int() =====
old3 = '            progress = int(value.upload_size / max(value.total_size, 1) * 100)'
new3 = '            try:\n                progress = int(value.upload_size / max(value.total_size, 1) * 100)\n            except ValueError:\n                progress = 0'
if old3 in content:
    content = content.replace(old3, new3, 1)
    print("Fix 3: upload progress int() protection applied")
else:
    print("Fix 3: pattern NOT FOUND")

open('module/pyrogram_extension.py', 'w').write(content)
print('ALL DONE')
