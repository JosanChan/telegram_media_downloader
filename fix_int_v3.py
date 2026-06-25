content = open('module/pyrogram_extension.py', 'r').read()

# === Fix 1: cloud_drive percentage int() protection (already done) ===
# Skip - already applied in fix_int_v2.py

# === Fix 2: download progress int() ===
old2 = '                progress = int(value["down_byte"] / value["total_size"] * 100)'
new2 = '                try:\n                    progress = int(value["down_byte"] / max(value["total_size"], 1) * 100)\n                except ValueError:\n                    progress = 0'
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("Fix 2 applied")
else:
    print("Fix 2 NOT FOUND")

# === Fix 3: upload progress int() ===
old3 = '            progress = int(value.upload_size / value.total_size * 100)'
new3 = '            try:\n                progress = int(value.upload_size / max(value.total_size, 1) * 100)\n            except ValueError:\n                progress = 0'
if old3 in content:
    content = content.replace(old3, new3, 1)
    print("Fix 3 applied")
else:
    print("Fix 3 NOT FOUND")

open('module/pyrogram_extension.py', 'w').write(content)
print('DONE')
