# -*- coding: utf-8 -*-
"""Search Bing (international) for Doppler NDT company, parse result links."""
import urllib.request
import urllib.parse
import re
import ssl
import json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def bing_search(q, mkt='zh-CN'):
    url = 'https://www.bing.com/search?q=' + urllib.parse.quote(q) + '&mkt=' + mkt + '&count=20'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
    })
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.read().decode('utf-8', 'ignore')

def parse_results(html):
    # <li class="b_algo"> ... <h2><a href="URL">TITLE</a></h2> ... <p>SNIPPET</p>
    items = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, flags=re.S):
        block = m.group(0)
        hm = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not hm:
            continue
        url = hm.group(1)
        title = re.sub(r'<[^>]+>', '', hm.group(2)).strip()
        pm = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.S)
        snip = re.sub(r'<[^>]+>', '', pm.group(1)).strip() if pm else ''
        items.append({'title': title, 'url': url, 'snippet': snip})
    return items

queries = [
    ('多浦乐 相控阵 超声检测', 'zh-CN'),
    ('广州多浦乐电子科技有限公司', 'zh-CN'),
    ('多浦乐 doppler 无损检测 探头', 'zh-CN'),
    ('Guangzhou Doppler ultrasonic NDT', 'en-US'),
]

out = []
for q, mkt in queries:
    out.append('#### QUERY: %s (%s)' % (q, mkt))
    try:
        html = bing_search(q, mkt)
        items = parse_results(html)
        if not items:
            out.append('  (no parsed results, html len=%d)' % len(html))
        for it in items[:10]:
            out.append('  - %s' % it['title'])
            out.append('    %s' % it['url'])
            out.append('    %s' % it['snippet'][:200])
    except Exception as e:
        out.append('  ERROR: %s' % repr(e)[:200])
    out.append('')

with open(r'C:\Users\Public\bing_results.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
