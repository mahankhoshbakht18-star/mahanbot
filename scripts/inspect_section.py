import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('static/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

marker = 'id="view-bank-select"'
start = text.find(marker)
end = text.find('</section>', start)
print(text[start:start+1200])
print("\n...[MIDDLE]...\n")
print(text[end-800:end+10])
