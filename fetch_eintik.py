# -*- coding: utf-8 -*-
"""Fetch eintik.cn product pages and extract main content (strip nav)."""
import urllib.request
import re
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.read().decode('utf-8', 'ignore')

pages = [
    'https://eintik.cn/ut-probes/',
    'https://eintik.cn/paut-probes/',
    'https://eintik.cn/med-array/',
    'https://eintik.cn/med-ice/',
    'https://eintik.cn/med-ivus/',
    'https://eintik.cn/endo-probes/',
    'https://eintik.cn/scanners/',
    'https://eintik.cn/wedges/',
    'https://eintik.cn/crystal/',
    'https://eintik.cn/m2probe/',
    'https://eintik.cn/oem-odm/',
]

out = []
for u in pages:
    try:
        html = fetch(u)
    except Exception as e:
        out.append('=== %s\nERROR: %s\n' % (u, e))
        continue
    # remove scripts/styles
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S|re.I)
    # capture main content region if present
    m = re.search(r'<main[^>]*>(.*?)</main>', html, flags=re.S|re.I)
    body = m.group(1) if m else html
    # extract text
    text = re.sub(r'<[^>]+>', '\n', body)
    text = re.sub(r'\n\s*\n+', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    # dedupe consecutive identical lines
    dedup = []
    for l in lines:
        if not dedup or dedup[-1] != l:
            dedup.append(l)
    out.append('=== %s' % u)
    out.append('\n'.join(dedup))
    out.append('')

with open(r'C:\Users\Public\eintik_pages.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('OK pages=%d' % len(pages))
