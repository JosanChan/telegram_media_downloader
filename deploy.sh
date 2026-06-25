#!/bin/bash
set -e

echo "=== 1/5: Syntax Check ==="
python3 -c "import py_compile; py_compile.compile('module/bot.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('module/app.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('module/pyrogram_extension.py', doraise=True)"
echo "Syntax OK"

echo "=== 2/5: Git Commit + Push ==="
git add -A
if git diff --cached --quiet; then
    echo "Nothing to commit"
else
    git commit -m "fix: auto deploy $(date +%Y%m%d-%H%M%S)"
fi
git push origin master

echo "=== 3/5: Docker Build ==="
docker build -t josanchan/telegram-downloader:latest .

echo "=== 4/5: Deploy ==="
cd ~/app
docker-compose -f docker-compose.yaml down
docker-compose -f docker-compose.yaml up -d

echo "=== 5/5: Verify ==="
sleep 3
docker logs app_telegram_media_downloader_1 --tail 5
echo ""
echo "Deploy complete!"

# 回滚模式
if [ "${1:-}" = "--rollback" ]; then
    echo "Rolling back..."
    for f in module/bot.py module/pyrogram_extension.py module/app.py; do
        if [ -f "$f.bak" ]; then
            cp "$f.bak" "$f" && echo "  Restored $f"
        fi
    done
    echo "Rebuilding..."
    docker build -t josanchan/telegram-downloader:latest .
    cd ~/app
    docker-compose -f docker-compose.yaml down
    docker-compose -f docker-compose.yaml up -d
    docker logs app_telegram_media_downloader_1 --tail 3
    exit 0
fi
