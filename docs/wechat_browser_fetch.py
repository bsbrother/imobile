#!/usr/bin/env python3
"""Fetch WeChat article via headless browser + save as markdown."""
import asyncio, re, os, sys
from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else 'https://mp.weixin.qq.com/s/YnYSOTH8ezySPoGnlQKcLw'

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL, wait_until='networkidle', timeout=30000)
        
        # Wait for js_content to appear
        try:
            await page.wait_for_selector('#js_content', timeout=15000)
        except:
            pass
        
        html = await page.content()
        title = await page.title()
        
        # Extract metadata from JS vars in the rendered page
        meta = await page.evaluate("""() => {
            const d = {};
            d.title = document.querySelector('.rich_media_title')?.innerText || '';
            d.author = document.querySelector('#js_author_name')?.innerText || '';
            d.pub_date = document.querySelector('#publish_time')?.innerText || '';
            d.account = document.querySelector('.profile_nickname')?.innerText || '';
            d.content = document.querySelector('#js_content')?.innerHTML || '';
            return d;
        }""")
        
        await browser.close()
        
        if not meta['content'] and not meta['title']:
            print("ERROR: No content extracted")
            sys.exit(1)
        
        return html, meta

html, meta = asyncio.run(main())

# --- Convert to Markdown ---
from bs4 import BeautifulSoup, NavigableString

soup = BeautifulSoup(meta['content'], 'html.parser')

def clean_text(el):
    if not el: return ''
    if isinstance(el, str): return el.strip()
    return re.sub(r'\s+', ' ', el.get_text(separator=' ')).strip()

def convert_node(el):
    if el is None: return ''
    if isinstance(el, NavigableString):
        s = str(el).strip()
        return s if s and s != '\xa0' else ''
    tag = el.name
    if tag in ('script','style','mp-common-cpsad','mp-style-type'): return ''
    if tag in ('h1','h2','h3','h4'):
        if tag == 'h1' and el.get('data-tool') == 'mdnice编辑器':
            return flatten_children(el)
        text = clean_text(el)
        return f"\n{'#' * int(tag[1])} {text}\n\n"
    if tag == 'blockquote': return f"\n> {clean_text(el)}\n\n"
    if tag == 'pre':
        code_el = el.find('code')
        code_text = code_el.get_text() if code_el else el.get_text()
        return f"\n```python\n{code_text}\n```\n\n"
    if tag == 'ul':
        items = []
        for li in el.find_all('li', recursive=False):
            text = re.sub(r'^[•·]\s*', '', clean_text(li))
            if text: items.append(f"- {text}")
        return '\n'.join(items) + '\n\n' if items else ''
    if tag == 'ol':
        items = []
        for i, li in enumerate(el.find_all('li', recursive=False), 1):
            text = re.sub(r'^\d+[.、)]\s*', '', clean_text(li))
            if text: items.append(f"{i}. {text}")
        return '\n'.join(items) + '\n\n' if items else ''
    if tag == 'p':
        style = el.get('style','')
        if 'font-size: 0px' in style or 'font-size:0px' in style: return ''
        text = clean_text(el)
        if not text or text == '\xa0': return ''
        return f"{text}\n\n"
    if tag == 'img':
        src = el.get('data-src') or el.get('src','')
        return f"\n![image]({src})\n\n"
    if tag in ('span','strong','em','b','i','a','code','sub','sup'):
        return flatten_children(el)
    if tag in ('section','div','hgroup','body','li'):
        return flatten_children(el)
    return flatten_children(el)

def flatten_children(el):
    parts = []
    for child in el.children:
        part = convert_node(child)
        if part: parts.append(part)
    return ''.join(parts)

title = meta['title'] or 'Untitled'
md_parts = [
    f"# {title}\n",
    f"**作者**: {meta['author']} | **日期**: {meta['pub_date']}\n",
    f"**来源**: {meta['account']}\n",
    f"**原文链接**: {URL}\n",
    "\n---\n"
]

for child in soup.children:
    if isinstance(child, NavigableString): continue
    result = convert_node(child)
    if result: md_parts.append(result)

md = ''.join(md_parts)
md = re.sub(r'\n{3,}', '\n\n', md)
md = re.sub(r' +\n', '\n', md)
md = md.strip() + '\n'

title_safe = re.sub(r'[?？/*:\"<>|「」\u201c\u201d\u2018\u2019]', '', title[:50]).strip()
author_safe = meta['account'].replace('/', '-') if meta['account'] else 'unknown'
date_safe = meta['pub_date'].replace('/', '-') if meta['pub_date'] else 'nodate'
filename = f"{date_safe}_{author_safe}_{title_safe}.md"
outpath = os.path.join('/home/kasm-user/apps/imobile/docs', filename)

with open(outpath, 'w', encoding='utf-8') as f:
    f.write(md)

print(f"OK: {outpath}")
print(f"Size: {len(md)} chars, {md.count(chr(10))} lines")
