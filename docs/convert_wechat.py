#!/usr/bin/env python3
"""Convert WeChat article HTML to clean markdown."""
import subprocess, json, sys, re, os

URL = sys.argv[1] if len(sys.argv) > 1 else 'https://mp.weixin.qq.com/s/YnYSOTH8ezySPoGnlQKcLw'

result = subprocess.run(
    ['node', '-e', f"""
const {{extract}} = require('/home/kasm-user/.hermes/skills/wechat-article-extractor/scripts/extract.js');
extract('{URL}').then(r => {{
  if (r.done && r.data) {{
    const d = r.data;
    console.log(JSON.stringify({{title:d.msg_title,author:d.msg_author,date:d.msg_publish_time_str,account:d.account_name,content:d.msg_content}}));
  }} else {{ console.log(JSON.stringify({{error:r}})); }}
}}).catch(e => console.log(JSON.stringify({{error: e.message}})));
"""],
    capture_output=True, text=True, timeout=30
)
data = json.loads(result.stdout)
if 'error' in data:
    print(f"ERROR: {data['error']}")
    sys.exit(1)

from bs4 import BeautifulSoup, NavigableString
soup = BeautifulSoup(data['content'], 'html.parser')

def clean_text(el):
    if not el: return ''
    if isinstance(el, str): return el.strip()
    t = el.get_text(separator=' ')
    return re.sub(r'\s+', ' ', t).strip()

def convert_node(el):
    if el is None: return ''
    if isinstance(el, NavigableString):
        s = str(el).strip()
        return s if s and s != '\xa0' else ''

    tag = el.name
    if tag in ('script','style','mp-common-cpsad','mp-style-type','mpcps'):
        return ''

    if tag in ('h1','h2','h3','h4'):
        if tag == 'h1' and el.get('data-tool') == 'mdnice编辑器':
            return flatten_children(el)
        text = clean_text(el)
        level = int(tag[1])
        return f"\n{'#' * level} {text}\n\n"

    if tag == 'blockquote':
        return f"\n> {clean_text(el)}\n\n"

    if tag == 'pre':
        code_el = el.find('code')
        code_text = code_el.get_text() if code_el else el.get_text()
        return f"\n```python\n{code_text}\n```\n\n"

    if tag == 'ul':
        items = []
        for li in el.find_all('li', recursive=False):
            text = clean_text(li)
            text = re.sub(r'^[•·]\s*', '', text)
            if text: items.append(f"- {text}")
        return '\n'.join(items) + '\n\n' if items else ''

    if tag == 'ol':
        items = []
        for i, li in enumerate(el.find_all('li', recursive=False), 1):
            text = clean_text(li)
            text = re.sub(r'^\d+[.、)]\s*', '', text)
            if text: items.append(f"{i}. {text}")
        return '\n'.join(items) + '\n\n' if items else ''

    if tag == 'p':
        style = el.get('style', '')
        if 'font-size: 0px' in style or 'font-size:0px' in style: return ''
        children = [c for c in el.children if not (isinstance(c, str) and str(c).strip() == '')]
        a_children = [c for c in children if hasattr(c, 'name') and c.name == 'a']
        if a_children and len(a_children) == len(children):
            links = []
            for a in a_children:
                href = a.get('href','')
                title = a.get('textvalue') or clean_text(a)
                if title and title != '\xa0': links.append(f"- [{title}]({href})")
            return '\n'.join(links) + '\n\n' if links else ''
        text = clean_text(el)
        if not text or text == '\xa0': return ''
        return f"{text}\n\n"

    if tag == 'img':
        src = el.get('data-src') or el.get('src','')
        return f"\n![image]({src})\n\n"

    if tag in ('span','strong','em','b','i','a','code','sub','sup'):
        return flatten_children(el)

    if tag in ('section','div','hgroup','body','html','[document]','li'):
        return flatten_children(el)

    return flatten_children(el)

def flatten_children(el):
    parts = []
    for child in el.children:
        part = convert_node(child)
        if part: parts.append(part)
    return ''.join(parts)

md_parts = []
md_parts.append(f"# {data['title']}\n")
md_parts.append(f"**来源**: {data['account']} | **日期**: {data['date']}\n")
md_parts.append(f"**原文链接**: {URL}\n")
md_parts.append("\n---\n")
for child in soup.children:
    if isinstance(child, NavigableString): continue
    result = convert_node(child)
    if result: md_parts.append(result)

md = ''.join(md_parts)
md = re.sub(r'\n{3,}', '\n\n', md)
md = re.sub(r' +\n', '\n', md)
md = md.strip() + '\n'

date_str = data['date'].split(' ')[0].replace('/', '-')
title_safe = re.sub(r'[?？/*:\"<>|「」\u201c\u201d\u2018\u2019]', '', data['title'][:50]).strip()
filename = f"{date_str}_{data['account']}_{title_safe}.md"
outpath = os.path.join('/home/kasm-user/apps/imobile/docs', filename)

with open(outpath, 'w', encoding='utf-8') as f:
    f.write(md)
print(f"OK: {outpath}")
print(f"Size: {len(md)} chars, {md.count(chr(10))} lines")
