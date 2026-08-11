# -*- coding: utf-8 -*-
"""Crawl cndoppler.com - find product URLs and probe pages."""
import urllib.request
import urllib.parse
import re
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9'
    })
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.read().decode('utf-8', 'ignore')

out = []
home = fetch('https://www.cndoppler.com/')
# collect all internal links
links = set()
for m in re.finditer(r'href="(https://www\.cndoppler\.com[^"#?]*)"', home):
    links.add(m.group(1))
for m in re.finditer(r'href="(/[^"#?]*)"', home):
    links.add('https://www.cndoppler.com' + m.group(1))
out.append('=== HOME LINKS (%d) ===' % len(links))
for l in sorted(links):
    out.append(l)

with open(r'C:\Users\Public\doppler_links.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE', len(links))
