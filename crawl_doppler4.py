# -*- coding: utf-8 -*-
"""Fetch cndoppler.com sitemap / search for probe pages."""
import urllib.request
import re
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
    })
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.read().decode('utf-8', 'ignore')

out = []
# try sitemaps
for sm in ['https://www.cndoppler.com/sitemap.xml',
           'https://www.cndoppler.com/sitemap.txt',
           'https://www.cndoppler.com/robots.txt']:
    try:
        t = fetch(sm)
        out.append('=== %s ===\n%s' % (sm, t[:3000]))
    except Exception as e:
        out.append('FAIL %s %s' % (sm, repr(e)[:60]))

# search Bing site: for probe pages
q = 'site:cndoppler.com 探头'
url = 'https://www.bing.com/search?q=' + urllib.parse.quote(q)
try:
    html = fetch(url)
    out.append('\n=== BING SITE SEARCH: 探头 ===')
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, flags=re.S):
        block = m.group(0)
        hm = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if hm:
            out.append('  %s | %s' % (hm.group(1), re.sub(r'<[^>]+>', '', hm.group(2)).strip()))
except Exception as e:
    out.append('BING FAIL %s' % repr(e)[:100])

q2 = 'site:cndoppler.com 相控阵探头 OR 常规探头 OR 换能器'
url2 = 'https://www.bing.com/search?q=' + urllib.parse.quote(q2)
try:
    html = fetch(url2)
    out.append('\n=== BING SITE SEARCH: 换能器/相控阵探头 ===')
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, flags=re.S):
        block = m.group(0)
        hm = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if hm:
            out.append('  %s | %s' % (hm.group(1), re.sub(r'<[^>]+>', '', hm.group(2)).strip()))
except Exception as e:
    out.append('BING2 FAIL %s' % repr(e)[:100])

with open(r'C:\Users\Public\doppler_sitemap.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
