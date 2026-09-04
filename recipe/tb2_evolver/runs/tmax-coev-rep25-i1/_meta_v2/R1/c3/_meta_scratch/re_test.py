import re
REDIRECT = re.compile(r"(?<![<>2&\d])>{1,2}\s*([^\s;&|><\n'\"]+)")
cmd = "cat > /home/user/deployment_monitor.py << 'EOF'\nSIZE_THRESHOLD = 40 * 1024 * 1024\nEOF"
print("heredoc redirect matches:", REDIRECT.findall(cmd))
cmd2 = "tee /tmp/ocr.py > /dev/null << PYEOF"
print("tee redirect matches:", REDIRECT.findall(cmd2))
