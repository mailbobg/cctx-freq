"""Keep inventory labels and conflicts; never infer executable observation time."""
import csv
from datetime import datetime,timedelta
import io

from r21_basis import number

FIELDS=('create_time','symbol','sum_open_interest','sum_open_interest_value',
    'count_toptrader_long_short_ratio','sum_toptrader_long_short_ratio',
    'count_long_short_ratio','sum_taker_long_short_vol_ratio')


def parse_metrics(body,symbol,file_day):
    reader=csv.reader(io.StringIO(body.decode('utf-8')))
    if tuple(next(reader))!=FIELDS:raise ValueError('Native inventory header differs')
    start=datetime.strptime(file_day,'%Y-%m-%d');stop=start+timedelta(days=1);rows=[];last=None
    for fields in reader:
        if len(fields)!=len(FIELDS):raise ValueError('Native inventory row shape differs')
        row=dict(zip(FIELDS,fields))
        if row['symbol']!=symbol:raise ValueError('Native symbol identity differs')
        try:label=datetime.strptime(row['create_time'],'%Y-%m-%d %H:%M:%S')
        except ValueError as exc:raise ValueError('Native label format differs') from exc
        if label.strftime('%Y-%m-%d %H:%M:%S')!=row['create_time'] or not start<=label<=stop or label.second or label.minute%5:
            raise ValueError('Native label file/grid differs')
        if last is not None and label<last:raise ValueError('Native labels unordered')
        for field in FIELDS[2:]:number(row[field])
        if number(row['sum_open_interest'])<=0 or number(row['sum_open_interest_value'])<=0:raise ValueError('Inactive/invalid native inventory')
        # Naive native labels are intentionally not assigned a time zone,
        # shifted by five minutes, or treated as a public observation receipt.
        row['label']=label;rows.append(row);last=label
    if not rows:raise ValueError('Empty original inventory source')
    return rows


def label_groups(rows):
    groups={}
    for row in rows:
        native={field:row[field] for field in FIELDS};groups.setdefault(row['label'],[]).append(native)
    return [dict(label=label,original_rows=len(values),identical_native_fields=all(v==values[0] for v in values),
                 native_values=values) for label,values in groups.items()]
