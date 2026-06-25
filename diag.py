lines = open('module/pyrogram_extension.py', 'r').readlines()

for i, line in enumerate(lines):
    if 'if not node.reply_message_id or not node.bot:' in line:
        indent = ' ' * 4
        log_line = indent + 'logger.warning("[DEBUG] report_bot_status: reply_id=" + str(node.reply_message_id) + " bot=" + str(node.bot))\n'
        lines.insert(i, log_line)
        print("Diagnostic log inserted at line", i+1)
        break

open('module/pyrogram_extension.py', 'w').writelines(lines)
print('DONE')
