lines = open('module/bot.py', 'r').readlines()
for i, line in enumerate(lines):
    # 缓冲循环中 stat_forward 前面加 if not album_mode
    if 'node.stat_forward(ForwardStatus.SuccessForward)' in line and i > 970:
        indent = ' ' * 20
        lines[i] = indent + 'if not node.forward_album_mode:\n'
        lines.insert(i+1, indent + '    node.stat_forward(ForwardStatus.SuccessForward)\n')
        break
open('module/bot.py', 'w').writelines(lines)
print('Done')
