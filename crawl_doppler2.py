# -*- coding: utf-8 -*-
"""Fetch cndoppler.com product pages and extract text."""
import urllib.request
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

def extract(url):
    html = fetch(url)
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S|re.I)
    text = re.sub(r'<[^>]+>', '\n', html)
    text = re.sub(r'\n\s*\n+', '\n', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    # dedupe consecutive
    dedup = []
    for l in lines:
        if not dedup or dedup[-1] != l:
            dedup.append(l)
    return '\n'.join(dedup)

pages = [
    'https://www.cndoppler.com/products/',
    'https://www.cndoppler.com/products/ultrasonic/phased-array-ut/',
    'https://www.cndoppler.com/products/medical/probe-customization/',
]

out = []
for u in pages:
    out.append('########## %s' % u)
    try:
        out.append(extract(u))
    except Exception as e:
        out.append('ERROR: %s' % repr(e)[:200])
    out.append('')

with open(r'C:\Users\Public\doppler_products.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
