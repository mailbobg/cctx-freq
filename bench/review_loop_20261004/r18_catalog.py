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
