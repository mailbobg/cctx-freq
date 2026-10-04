"""Exact native report columns and vintage observations; never infer unit scales."""
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import io
import json
import re

from r31_sources import DATA, PROBES, retained, targets

LABEL = 'Reserve balances with Federal Reserve Banks'
SHORT_DATE = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}'


def compact(value):
    return re.sub(r'\s+', '', value)


class NativeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.pres, self.cells = [], [], {}
        self.pre = None
        self.cell = None
        self.cell_parts = []
        self.tables = []
        self.table_stack = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.tables.append(''.join(self.text)[-1000:])
            self.table_stack.append(len(self.tables)-1)
        if tag == 'pre':
            if self.pre is not None: raise ValueError('Nested native preformatted blocks')
            self.pre = []
        if tag in ('td','th'):
            if self.cell is not None: raise ValueError('Nested native table cells')
            self.cell = dict(attrs)
            self.cell_parts = []
            self.cell_table = self.table_stack[-1] if self.table_stack else None

    def handle_data(self, value):
        self.text.append(value)
        if self.pre is not None: self.pre.append(value)
        if self.cell is not None: self.cell_parts.append(value)

    def handle_endtag(self, tag):
        if tag == 'table':
            if not self.table_stack: raise ValueError('Unmatched native table close')
            self.table_stack.pop()
        if tag == 'pre':
            if self.pre is None: raise ValueError('Unmatched native preformatted close')
            self.pres.append(''.join(self.pre))
            self.pre = None
        if tag in ('td','th') and self.cell is not None:
            key = self.cell.get('id')
            if key:
                if key in self.cells: raise ValueError('Duplicate original table cell id')
                self.cells[key] = dict(text=''.join(self.cell_parts).strip(), attributes=self.cell,
                                      table_index=self.cell_table)
            self.cell = None


def integer(text):
    stripped = text.strip()
    if not re.fullmatch(r'[+-]?\s*(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)', stripped):
        raise ValueError('Invalid/unknown original signed integer')
    return int(re.sub(r'[\s,]', '', stripped))


def short_date(text):
    return datetime.strptime(re.sub(r'\s+', ' ', text.strip()), '%b %d, %Y').date()


def parse_publisher(body, release):
    day = date.fromisoformat(release)
    if day.isoformat() != release or day.weekday() not in (3,4):
        raise ValueError('Unreviewed publisher release calendar identity')
    parser = NativeHTML()
    parser.feed(body.decode('utf-8'))
    whole = ''.join(parser.text)
    long_day = day.strftime('%B')+' '+str(day.day)+', '+str(day.year)
    if compact(long_day) not in compact(whole) or 'Factors Affecting Reserve Balances' not in whole:
        raise ValueError('Original publisher release identity differs')
    expected = day-timedelta(days=day.weekday()-2)
    if parser.pres:
        if len(parser.pres) != 1:
            raise ValueError('Unreviewed multiple original preformatted reports')
        text = parser.pres[0]
        rows = [line for line in text.splitlines() if line.lstrip().startswith(LABEL)]
        if len(rows) != 1:
            raise ValueError('Original reserve label scope differs')
        line = rows[0]
        row_start = text.index(line)
        beginning = text.rfind('1. Factors Affecting Reserve Balances',0,row_start)
        header = text[beginning:row_start]
        if beginning < 0 or 'Millions of dollars' not in header or 'Averages of daily figures' not in header:
            raise ValueError('Original units/average header differs')
        dates = re.findall(r'Federal Reserve Banks\s+('+SHORT_DATE+r')\s+('+SHORT_DATE+r')\s+('+SHORT_DATE+r')',header)
        if len(dates) != 1:
            raise ValueError('Original average/change column dates ambiguous')
        observation, previous, prior_year = map(short_date,dates[0])
        fields = re.fullmatch(r'\s*(\d[\d,]*)\s+([+-]\s*[\d,]+|0)\s+([+-]\s*[\d,]+|0)\s+(\d[\d,]*)\s*',line.lstrip()[len(LABEL):])
        if fields is None:
            raise ValueError('Original reserve row has foreign/unknown columns')
        native = list(fields.groups())
        average, change_week, change_year, wednesday = map(integer,native)
        original_columns = dict(layout='preformatted_ascii', full_native_line=line, native_columns=native)
    else:
        matches = [key for key,value in parser.cells.items() if compact(value['text']) == compact(LABEL)]
        if len(matches) != 1 or not re.fullmatch(r't2r\d+c1',matches[0]):
            raise ValueError('Original table1continued reserve scope differs')
        prefix = matches[0][:-2]
        c = parser.cells
        context = parser.tables[c[matches[0]]['table_index']]
        if (compact('1. Factors Affecting Reserve Balances of Depository Institutions (continued)') not in compact(context) or
            compact('Millions of dollars') not in compact(context) or
            compact(c['t2h1c2']['text']) != compact('Averages of daily figures') or
            compact(c['t2h2c2']['text']) != compact('Change from week ended')):
            raise ValueError('Original units/average/change table header differs')
        observation_text = c['t2h2c1']['text']
        stock_text = c['t2h1c3']['text']
        if not observation_text.startswith('Week ended') or not stock_text.startswith('Wednesday'):
            raise ValueError('Original average versus stock heading differs')
        observation = short_date(observation_text[len('Week ended'):])
        if short_date(stock_text[len('Wednesday'):]) != observation:
            raise ValueError('Original Wednesday-stock and week-end dates differ')
        previous, prior_year = short_date(c['t2h3c1']['text']), short_date(c['t2h3c2']['text'])
        expected_refs = ('t2r1c1 t2h1c2 t2h2c1','t2r1c1 t2h1c2 t2h2c2 t2h3c1',
                         't2r1c1 t2h1c2 t2h2c2 t2h3c2','t2r1c1 t2h1c3')
        cells = [c[prefix+'c'+str(i)] for i in range(2,6)]
        if any(' '.join(x['attributes'].get('header','').split()) != ref for x,ref in zip(cells,expected_refs)):
            raise ValueError('Original column semantic references differ')
        native = [x['text'] for x in cells]
        average,change_week,change_year,wednesday = map(integer,native)
        original_columns = dict(layout='native_html_cells', native_columns=native, original_cells=cells,
                                original_preceding_table_title_and_unit_context=context,
                                original_header_cells={key:c[key] for key in ('t2r1c1','t2h1c2','t2h1c3','t2h2c1','t2h2c2','t2h3c1','t2h3c2')})
    if observation != expected or previous != observation-timedelta(days=7) or not 350 <= (observation-prior_year).days <= 380:
        raise ValueError('Actual native observation/change dates differ from source calendar')
    if average < 0 or wednesday < 0:
        raise ValueError('Negative reserve quantity')
    return dict(release_date=release, observation_date=observation.isoformat(),
        previous_week_date=previous.isoformat(),prior_year_week_date=prior_year.isoformat(),
        native_units='Millions of dollars',week_average_million_usd=average,
        signed_week_change_million_usd=change_week,signed_year_change_million_usd=change_year,
        wednesday_level_million_usd=wednesday,original_columns=original_columns,
        source_body_sha256=hashlib.sha256(body).hexdigest(),actual_first_public_receipt_certified=False)


def parse_archive(body, vintage):
    day = date.fromisoformat(vintage)
    if day.isoformat()!=vintage or day.weekday()!=5:
        raise ValueError('Unknown native Saturday vintage identity')
    reader = csv.reader(io.StringIO(body.decode('utf-8')))
    if next(reader,None) != ['observation_date','WRESBAL_'+vintage.replace('-','')]:
        raise ValueError('Native CSV series/version/schema differs')
    rows, previous = {}, None
    for fields in reader:
        if len(fields) != 2:
            raise ValueError('Native CSV column count differs')
        observation = date.fromisoformat(fields[0])
        if observation.isoformat()!=fields[0] or observation.weekday()!=2 or observation>day or previous is not None and observation<=previous:
            raise ValueError('Duplicate/unordered/future/foreign weekly observation')
        if fields[1] not in ('','.') and not re.fullmatch(r'\d+(?:\.\d+)?',fields[1]):
            raise ValueError('Invalid original nonnegative decimal quantity')
        rows[fields[0]]=fields[1]
        previous=observation
    if not rows: raise ValueError('Empty native archive is unavailable')
    outside = [d for d in rows if not day-timedelta(days=41)<=date.fromisoformat(d)<=day]
    return dict(vintage=vintage,rows=rows,native_rows=len(rows),outside_requested_interval_dates=outside,
                native_csv_contains_unit_metadata=False,source_body_sha256=hashlib.sha256(body).hexdigest())


def reconcile_same_units(publisher,archive):
    value = archive['rows'].get(publisher['observation_date'])
    if value in (None,'','.'): raise ValueError('Required exact native reserve observation unavailable')
    if Decimal(value) != publisher['week_average_million_usd']:
        raise ValueError('Unverified native archive units/value differ from publisher millions')
    return dict(same_unit_value_exact=True,observation_date=publisher['observation_date'])


def observe():
    manifest=json.loads((DATA/'manifest.json').read_text())
    scope=targets()
    if manifest['source_plan_commit']!='f93dd94' or set(manifest['sources'])!=set(scope):
        raise ValueError('Original twenty-role source scope differs')
    bodies={}
    for role,record in manifest['sources'].items():
        if record['url']!=scope[role]: raise ValueError('Original source URL differs')
        bodies[role]=retained(record)
        if record['status']!=200 or bodies[role] is None: raise ValueError('Required original source unavailable '+role)
    reports={key:parse_publisher(value,key[10:]) for key,value in bodies.items() if re.fullmatch(r'publisher_\d{4}-\d{2}-\d{2}',key)}
    archives={key:parse_archive(value,key[8:]) for key,value in bodies.items() if key.startswith('archive_')}
    comparisons=[]
    for monday,current,prior in PROBES:
        vintage=(date.fromisoformat(monday)-timedelta(days=2)).isoformat()
        archive=archives['archive_'+vintage]
        for release in (current,prior):
            publisher=reports['publisher_'+release]
            native=archive['rows'].get(publisher['observation_date'])
            try:
                reconcile_same_units(publisher,archive)
                error=None
            except ValueError as exc:
                error=str(exc)
            comparisons.append(dict(monday=monday,vintage=vintage,release=release,
                observation_date=publisher['observation_date'],native_archive_text=native,
                publisher_average_million_usd=publisher['week_average_million_usd'],
                same_unit_contract_pass=error is None,original_source_contract_failure=error,
                no_scale_inferred_or_native_value_changed=True))
    result=dict(source_plan_commit='f93dd94',source_only=True,no_alpha_rule_cohort_weights_or_performance=True,
        original_roles=20,all_native_responses_http200=True,original_publisher_reports=reports,
        original_archives=archives,original_same_units_comparisons=comparisons,
        same_unit_contract_gate=all(x['same_unit_contract_pass'] for x in comparisons),
        same_unit_comparisons_passed=sum(x['same_unit_contract_pass'] for x in comparisons),
        historical_unit_metadata_still_required=True,adopted=False,
        native_input_sha256={'manifest.json':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),
            **{r['path']:r['sha256'] for r in manifest['sources'].values()}})
    (DATA.parent/'r31_initial_source_observation.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({key:result[key] for key in ('original_roles','same_unit_contract_gate','same_unit_comparisons_passed','historical_unit_metadata_still_required','adopted')},indent=2))
    return result


if __name__=='__main__':observe()
