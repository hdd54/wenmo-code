# -*- coding: utf-8 -*-
"""Find and fetch cndoppler.com probe sub-pages + search TP series."""
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

# 1. discover product sub-page links
html = fetch('https://www.cndoppler.com/products/')
links = set()
for m in re.finditer(r'href="(https://www\.cndoppler\.com[^"#?]*)"', html):
    links.add(m.group(1))
for m in re.finditer(r'href="(/[^"#?]*)"', html):
    links.add('https://www.cndoppler.com' + m.group(1))
out.append('=== PRODUCT PAGE LINKS ===')
for l in sorted(links):
    if any(k in l.lower() for k in ['probe', '探头', 'transducer', 'product', 'medical', 'ultrasonic', 'sensor']):
        out.append(l)

# 2. probe sub-pages: try common patterns for 相控阵探头 / 常规探头
candidates = [
    'https://www.cndoppler.com/products/ultrasonic/phased-array-probes/',
    'https://www.cndoppler.com/products/ultrasonic/conventional-probes/',
    'https://www.cndoppler.com/products/ultrasonic/probes/',
    'https://www.cndoppler.com/products/probes/',
    'https://www.cndoppler.com/products/sensor/',
    'https://www.cndoppler.com/products/sensors/',
    'https://www.cndoppler.com/products/medical/',
    'https://www.cndoppler.com/products/ultrasonic/',
]
out.append('\n=== PROBE SUB-PAGE PROBE ===')
for u in candidates:
    try:
        h = fetch(u)
        out.append('OK %s (len=%d)' % (u, len(h)))
    except Exception as e:
        out.append('FAIL %s %s' % (u, repr(e)[:60]))

with open(r'C:\Users\Public\doppler_probes.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
