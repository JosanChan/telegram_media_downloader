lines = open('module/pyrogram_extension.py', 'r').readlines()

# Fix 1: line 915 - int(value.percentage.split("%")[0])
for i, line in enumerate(lines):
    if 'int(value.percentage.split("%")[0])' in line:
        indent = ' ' * 16
        lines[i] = indent + 'try:\n'
        lines.insert(i+1, indent + '    pct = int(value.percentage.split("%")[0])\n')
        lines.insert(i+2, indent + 'except (ValueError, IndexError):\n')
        lines.insert(i+3, indent + '    pct = 0\n')
        # fix the f-string on the next line
        if i+4 < len(lines):
            lines[i+4] = lines[i+4].replace(
                'int(value.percentage.split("%")[0])', 'pct'
            )
        print(f"Fix 1: line {i+1}")
        break

# Fix 2: line 931 - int(value["down_byte"] / ...)
for i, line in enumerate(lines):
    if 'progress = int(value["down_byte"]' in line:
        indent = ' ' * 16
        lines[i] = indent + 'try:\n'
        lines.insert(i+1, indent + '    progress = int(value["down_byte"] / max(value["total_size"], 1) * 100)\n')
        lines.insert(i+2, indent + 'except ValueError:\n')
        lines.insert(i+3, indent + '    progress = 0\n')
        print(f"Fix 2: line {i+1}")
        break

# Fix 3: line 952 - int(value.upload_size / ...)
for i, line in enumerate(lines):
    if 'progress = int(value.upload_size / max(value.total_size' in line:
        indent = ' ' * 12
        lines[i] = indent + 'try:\n'
        lines.insert(i+1, indent + '    progress = int(value.upload_size / max(value.total_size, 1) * 100)\n')
        lines.insert(i+2, indent + 'except ValueError:\n')
        lines.insert(i+3, indent + '    progress = 0\n')
        print(f"Fix 3: line {i+1}")
        break

open('module/pyrogram_extension.py', 'w').writelines(lines)
print('ALL DONE')
