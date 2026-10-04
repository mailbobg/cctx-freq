"""Complete original editions, strict joins and an independent HTML-cell reader."""
from datetime import date,datetime,timedelta
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import pandas as pd
from r20_audit import retained
from r19_run import frozen_json
from r33_calendar import cohort,targets,latest,exact_prior,eligibility
from r33_sources import DATA,selected
from r33_quantity import parse_report,feature

HERE=Path(__file__).resolve().parent


class IndependentHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.cells={};self.whole=[];self.pres=[];self.active=None;self.pre=None
    def handle_starttag(self,tag,attrs):
        if tag=='pre':self.pre=[]
        if tag in ('td','th'):
            if self.active is not None:raise ValueError('Independent native cells nested')
            self.active=(dict(attrs),[])
    def handle_data(self,value):
        self.whole.append(value)
        if self.pre is not None:self.pre.append(value)
        if self.active is not None:self.active[1].append(value)
    def handle_endtag(self,tag):
        if tag=='pre' and self.pre is not None:self.pres.append(''.join(self.pre));self.pre=None
        if tag in ('td','th') and self.active is not None:
            attributes,parts=self.active
            if 'id' in attributes:
                if attributes['id'] in self.cells:raise ValueError('Independent duplicate native cell identity')
                self.cells[attributes['id']]=(attributes,''.join(parts))
            self.active=None


def independent_average(body,report):
    p=IndependentHTML();p.feed(body.decode('utf-8'))
    squeeze=lambda s:re.sub(r'\s+','',s)
    whole=''.join(p.whole);release=date.fromisoformat(report['release_date']);observation=date.fromisoformat(report['observation_date'])
    variants=[release.strftime('%B')+' '+str(release.day)+', '+str(release.year),release.strftime('%B %d, %Y')]
    if not any(squeeze(v) in squeeze(whole) for v in variants):raise ValueError('Independent native release identity differs')
    label='Reserve balances with Federal Reserve Banks'
    if p.pres:
        if len(p.pres)!=1:raise ValueError('Independent preformatted report count differs')
        native=p.pres[0];rows=[x for x in native.splitlines() if x.strip().startswith(label)]
        if len(rows)!=1:raise ValueError('Independent native reserve row differs')
        row=rows[0].strip();literal=row[len(label):].strip().split()[0]
        short=observation.strftime('%b')+' '+str(observation.day)+', '+str(observation.year)
        if squeeze(short) not in squeeze(native) or 'Millions of dollars' not in native:
            raise ValueError('Independent native observation or unit differs')
        identity=dict(layout='preformatted_ascii',original_full_line=rows[0],original_value_literal=literal)
    else:
        keys=[k for k,v in p.cells.items() if squeeze(v[1])==squeeze(label)]
        if len(keys)!=1:raise ValueError('Independent native report row identity differs')
        prefix=keys[0][:-2];attributes,literal=p.cells[prefix+'c2']
        if ' '.join(attributes.get('header','').split())!='t2r1c1 t2h1c2 t2h2c1':
            raise ValueError('Independent average cannot be Wednesday stock')
        raw=p.cells['t2h2c1'][1];short=observation.strftime('%b')+' '+str(observation.day)+', '+str(observation.year)
        # Native short day may have one or two digits; parse instead of assuming presentation padding.
        match=re.fullmatch(r'Week\s+ended\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4})',raw.strip())
        if match is None or datetime.strptime(re.sub(r'\s+',' ',match[1]),'%b %d, %Y').date()!=observation:
            raise ValueError('Independent exact native observation header differs')
        if squeeze('Millions of dollars') not in squeeze(whole):raise ValueError('Independent native unit unavailable')
        identity=dict(layout='native_html_cells',native_cell_id=prefix+'c2',original_attributes=attributes,original_value_literal=literal)
    literal=literal.strip()
    if not re.fullmatch(r'(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)',literal):
        raise ValueError('Independent unknown/negative reserve quantity')
    value=int(literal.replace(',',''))
    if value!=report['week_average_million_usd']:raise ValueError('Independent native average differs')
    return dict(**identity,observation_date=report['observation_date'],release_date=report['release_date'],
        exact_native_average=value,source_sha256=hashlib.sha256(body).hexdigest())


def source_inputs():
    originals=selected();records=json.loads((DATA/'manifest.json').read_text())['sources']
    reports={};independent={};hashes={}
    for role,(body,record,name) in originals.items():
        release=role[10:];report=parse_report(body,release)
        if report['source_body_sha256']!=record['sha256']:raise ValueError('Original publisher bytes differ')
        reports[release]=report
        independent[release]=dict(selected_record=name,**independent_average(body,report))
    if len(reports)!=296:raise ValueError('Complete native report population required before features')
    pairs=cohort();features={}
    for pair in pairs:
        value=feature(pair,reports);features[pd.Timestamp(pair['monday'],tz='UTC')]=value
    calendar=json.loads((HERE/'r32_source_audit.json').read_text())['native_calendar']
    for cutoff in ('2021-12-31','2024-12-31'):
        end=datetime.fromisoformat(cutoff+'T00:00:00+00:00')
        past_calendar=dict(releases={d:r for d,r in calendar['releases'].items() if eligibility(d)<=end})
        past_reports={d:r for d,r in reports.items() if eligibility(d)<=end}
        for pair in pairs:
            if datetime.fromisoformat(pair['monday']+'T00:00:00+00:00')>end:continue
            current=latest(past_calendar,pair['monday']);prior_day=(date.fromisoformat(current['expected_observation_date'])-timedelta(days=28)).isoformat()
            shortened=dict(window=pair['window'],monday=pair['monday'],current=current,
                prior=exact_prior(past_calendar,prior_day,pair['monday']))
            if feature(shortened,past_reports)!=features[pd.Timestamp(pair['monday'],tz='UTC')]:
                raise ValueError('Actual report/calendar prefix changed source feature')
    for role,r in records.items():
        retained(DATA,r)
        if r.get('path'):hashes['r33_sources/'+r['path']]=r['sha256']
    for name in ('r33_sources/manifest.json','r33_planned_sources.json','r32_publication_audit.json'):
        hashes[name]=hashlib.sha256((HERE/name).read_bytes()).hexdigest()
    hashes.update(json.loads((HERE/'r32_publication_audit.json').read_text())['native_input_sha256'])
    result=dict(protocol_commit='ff39936',source_stage_only=True,new_economic_outcomes=False,
        retained_attempts=len(records),original_report_editions=len(reports),decision_mondays=len(features),required_quantity_cells=584,
        failed_transport_attempts=sum(r['status'] is None for r in records.values()),
        inherited_original_captures=sum(bool(r.get('inherited')) for r in records.values()),
        original_reports=reports,independent_native_cell_joins=independent,
        source_feature_cohort={str(d):f for d,f in features.items()},source_inputs_sha256=hashes,
        full_native_source_contract_gate=True,actual_report_and_calendar_prefix_equal=True,
        modeled_buffer_not_original_first_public_receipt_or_edition_immutability=True,adopted=False)
    frozen_json(HERE/'r33_source_audit.json',result)
    validation=dict(source_only=True,new_economic_outcomes=False,original_editions=296,exact_required_cell_joins=584,
        independent_native_html_or_ascii_average_value_identity=True,actual_calendar_source_prefix_equal=True,
        no_premature_archival_vintage_substitution=True,first_public_receipt_or_unchanged_original_edition_certified=False)
    frozen_json(HERE/'r33_native_feature_validation.json',validation)
    return features,hashes


if __name__=='__main__':
    f,h=source_inputs();print(dict(native_decisions=len(f),native_inputs=len(h),full_native_source_contract_gate=True))
