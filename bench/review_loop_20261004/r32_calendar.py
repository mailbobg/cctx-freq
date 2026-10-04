"""Strict actual release listing, with first listed Thursday/Friday editions."""
from datetime import date, datetime, timedelta
import hashlib
import json
import re


def parse_calendar(body,captured_day):
    end=date.fromisoformat(captured_day)
    native=json.loads(body)
    if not isinstance(native,list) or not native:raise ValueError('Native release calendar unavailable')
    seen_years=set();out={}
    for yi,year in enumerate(native):
        if set(year)!={'yearValue','Months'} or not re.fullmatch(r'\d{4}',year['yearValue']) or year['yearValue'] in seen_years:
            raise ValueError('Native calendar year identity duplicated/unknown')
        seen_years.add(year['yearValue']);seen_months=set()
        for mi,month in enumerate(year['Months']):
            if set(month)!={'MonthName','MonthValue','Dates'} or not re.fullmatch(r'\d{6}',month['MonthValue']) or month['MonthValue'] in seen_months:
                raise ValueError('Native calendar month identity differs')
            seen_months.add(month['MonthValue'])
            try:number=datetime.strptime(month['MonthName'],'%B').month
            except ValueError as exc:raise ValueError('Unknown original month name') from exc
            if month['MonthValue']!=year['yearValue']+format(number,'02d') or not isinstance(month['Dates'],list) or not month['Dates']:
                raise ValueError('Original calendar year/month disagree')
            for di,text in enumerate(month['Dates']):
                if not isinstance(text,str) or not re.fullmatch(r'\d{8}',text) or not text.startswith(month['MonthValue']):
                    raise ValueError('Foreign native calendar date identity')
                day=datetime.strptime(text,'%Y%m%d').date();key=day.isoformat()
                if day>end or key in out:raise ValueError('Future or duplicated original release date')
                out[key]=dict(release_date=key,native_date_text=text,native_year_index=yi,
                    native_month_index=mi,native_day_index=di,
                    report_url='https://www.federalreserve.gov/releases/h41/'+text+'/')
    if not out:raise ValueError('Empty original release-date listing')
    return dict(releases=out,original_native_year_groups=len(native),original_release_dates=len(out),
        first_release_date=min(out),last_release_date=max(out),source_sha256=hashlib.sha256(body).hexdigest(),
        native_order_and_indices_retained=True,current_historical_listing_not_first_public_log=True)


def report_for_observation(calendar,observation):
    day=date.fromisoformat(observation)
    if day.isoformat()!=observation or day.weekday()!=2:raise ValueError('Required observation is not exact Wednesday')
    # This is a declared source-edition rule, not a fitted publication lag.
    candidates=[calendar['releases'][d.isoformat()] for d in (day+timedelta(days=1),day+timedelta(days=2))
                if d.isoformat() in calendar['releases']]
    if not candidates:raise ValueError('No actual listed Thursday/Friday publisher report for exact Wednesday')
    return candidates[0]
