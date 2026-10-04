"""Strict public current-catalog snapshot, never a historical event coverage gate."""
import json
import math
import re


def parse_page(raw):
    try:
        root=json.loads(raw);catalogs=root['data']['catalogs']
        if root['code']!='000000' or root['success'] is not True or len(catalogs)!=1:raise ValueError('Unsuccessful/wrong current catalog schema')
        catalog=catalogs[0];total=catalog['total'];rows=catalog['articles']
        if catalog['catalogId']!=161 or catalog['catalogName']!='Delisting' or type(total) is not int or total<0 or not isinstance(rows,list):
            raise ValueError('Current delisting catalog identity/total differs')
        for row in rows:
            if type(row['id']) is not int or row['id']<1 or not isinstance(row['code'],str) or not re.fullmatch('[0-9a-f]{32}',row['code']):
                raise ValueError('Original article identity differs')
            if not isinstance(row['title'],str) or not row['title'].strip() or row['type']!=1 or type(row['releaseDate']) is not int or not 10**12<=row['releaseDate']<10**14:
                raise ValueError('Original title/millisecond release clock differs')
        if len({r['id'] for r in rows})!=len(rows) or len({r['code'] for r in rows})!=len(rows):raise ValueError('Repeated source article')
    except (KeyError,TypeError,json.JSONDecodeError) as exc:raise ValueError('Unreviewed current catalog schema') from exc
    return total,rows


def stable_catalog(pages,repeated_first,page_size=50):
    if not pages or type(page_size) is not int or page_size<1:raise ValueError('Explicit source pages required')
    total=pages[0][0]
    if repeated_first!=pages[0] or len(pages)!=max(1,math.ceil(total/page_size)):raise ValueError('Unstable/partial current snapshot boundary')
    rows=[]
    for i,(declared,page) in enumerate(pages):
        expected=min(page_size,max(0,total-i*page_size))
        if declared!=total or len(page)!=expected:raise ValueError('Conflicting/partial source page')
        rows.extend(page)
    if len(rows)!=total or len({r['id'] for r in rows})!=total or len({r['code'] for r in rows})!=total:
        raise ValueError('Conflicting/repeated current catalog inventory')
    return rows


def body_text(body):
    try:tree=json.loads(body)
    except (TypeError,json.JSONDecodeError) as exc:raise ValueError('Unknown structured article body') from exc
    if not isinstance(tree,dict) or tree.get('node')!='root':raise ValueError('Unknown structured article body root')
    text=[]
    def walk(node):
        if not isinstance(node,dict) or node.get('node') not in ('root','element','text'):raise ValueError('Unknown article node')
        if node['node']=='text':
            if not isinstance(node.get('text'),str):raise ValueError('Unknown source text')
            text.append(node['text'])
        children=node.get('child',[])
        if not isinstance(children,list):raise ValueError('Unknown structured child list')
        for child in children:walk(child)
    walk(tree)
    return ' '.join(text)


def parse_detail(raw,code,title_pattern=None):
    try:
        root=json.loads(raw);data=root['data']
        if root['code']!='000000' or root['success'] is not True or not isinstance(data,dict) or data['code']!=code:
            raise ValueError('Article source identity differs')
        if not isinstance(data['title'],str) or not data['title'].strip():raise ValueError('Article title missing')
        if title_pattern is not None and re.search(title_pattern,data['title'],re.I) is None:raise ValueError('Source title does not match the forensic role')
        if type(data['publishDate']) is not int or not 10**12<=data['publishDate']<10**14 or data['articleType'] not in (1,2):
            raise ValueError('Original article publication label/schema differs')
        updated=data['lastUpdateTime']
        if type(updated) is not int or (updated!=0 and not 10**12<=updated<10**14):raise ValueError('Unknown last-update field schema')
        if not isinstance(data['body'],str) or not body_text(data['body']).strip():raise ValueError('Source body unknown/empty')
    except (KeyError,TypeError,json.JSONDecodeError) as exc:raise ValueError('Unknown article source schema') from exc
    # lastUpdateTime=0 is only an unknown/sentinel, not proof of no past edits.
    return data
