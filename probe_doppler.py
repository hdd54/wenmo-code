# -*- coding: utf-8 -*-
"""Probe candidate domains for Guangzhou Doppler (Duopule) NDT company."""
import urllib.request
import ssl
import socket

socket.setdefaulttimeout(8)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

candidates = [
    'www.dopplerndt.com', 'dopplerndt.com',
    'www.doppler-ndt.com', 'doppler-ndt.com',
    'www.dopplerndt.cn', 'dopplerndt.cn',
    'www.do-ndt.com', 'do-ndt.com',
    'www.do-ndt.cn', 'do-ndt.cn',
    'www.doppler.com.cn', 'doppler.com.cn',
    'www.dplndt.com', 'dplndt.com',
    'www.duopule.com', 'duopule.com',
    'www.dopplerndt.com.cn', 'dopplerndt.com.cn',
    'www.doppler-tech.com', 'doppler-tech.com',
    'www.gzdpl.com', 'gzdpl.com',
    'www.dpl-ndt.com', 'dpl-ndt.com',
    'www.dopplerphasedarray.com', 'dopplerphasedarray.com',
]

out = []
for d in candidates:
    for scheme in ('https', 'http'):
        url = scheme + '://' + d + '/'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
                body = r.read(2000).decode('utf-8', 'ignore')
                title = ''
                import re
                m = re.search(r'<title[^>]*>(.*?)</title>', body, flags=re.S|re.I)
                if m:
                    title = m.group(1).strip()[:100]
                out.append('OK   %s  [%s] title=%s' % (url, r.status, title))
                break
        except Exception as e:
            if 'HTTP Error' in repr(e):
                out.append('HTTP %s %s' % (repr(e)[-3:], url))
                break
            else:
                out.append('FAIL %s %s' % (url, repr(e)[:80]))
    else:
        continue

with open(r'C:\Users\Public\doppler_domains.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
