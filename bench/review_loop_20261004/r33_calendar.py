"""Uniform next-local-day-plus-one-hour arrival model for actual releases."""
from datetime import date,datetime,timedelta,time,timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from r31_cohort_sources import decision_pairs

HERE=Path(__file__).resolve().parent


def eligibility(release):
    day=date.fromisoformat(release)
    if day.isoformat()!=release:raise ValueError('Noncanonical actual release date')
    return datetime.combine(day+timedelta(days=1),time(1),ZoneInfo('America/New_York')).astimezone(timezone.utc)


def latest(calendar,monday):
    decision=datetime.fromisoformat(monday+'T00:00:00+00:00')
    if decision.weekday()!=0:raise ValueError('Source decision is not Monday')
    candidates=[r for d,r in calendar['releases'].items() if eligibility(d)<=decision]
    if not candidates:raise ValueError('No actual public-report arrival before decision')
    chosen=max(candidates,key=lambda r:r['release_date'])
    release=date.fromisoformat(chosen['release_date'])
    if release.weekday() not in (0,3,4):raise ValueError('Unreviewed actual report release weekday')
    observation=release-timedelta(days=(release.weekday()-2)%7)
    return dict(**chosen,expected_observation_date=observation.isoformat(),
                modeled_eligible_utc=eligibility(chosen['release_date']).isoformat())


def exact_prior(calendar,observation,decision):
    day=date.fromisoformat(observation)
    if day.weekday()!=2 or day.isoformat()!=observation:raise ValueError('Exact four-week comparator is not Wednesday')
    available=[calendar['releases'][d.isoformat()] for d in (day+timedelta(days=k) for k in range(1,8))
               if d.isoformat() in calendar['releases']]
    if not available:raise ValueError('Exact comparator has no actual issuing report')
    chosen=available[0]
    if date.fromisoformat(chosen['release_date']).weekday() not in (0,3,4):
        raise ValueError('Unreviewed comparator release weekday')
    if eligibility(chosen['release_date'])>datetime.fromisoformat(decision+'T00:00:00+00:00'):
        raise ValueError('Exact comparator report is not yet eligible')
    return dict(**chosen,expected_observation_date=observation,
                modeled_eligible_utc=eligibility(chosen['release_date']).isoformat())


def cohort():
    calendar=json.loads((HERE/'r32_source_audit.json').read_text())['native_calendar']
    out=[]
    for row in decision_pairs():
        current=latest(calendar,row['monday'])
        prior=(date.fromisoformat(current['expected_observation_date'])-timedelta(days=28)).isoformat()
        out.append(dict(window=row['window'],monday=row['monday'],current=current,
                        prior=exact_prior(calendar,prior,row['monday'])))
    return out


def targets():
    out={}
    for pair in cohort():
        for kind in ('current','prior'):
            r=pair[kind];out['publisher_'+r['release_date']]=r['report_url']
    if len(out)!=296:raise ValueError('Full report source scope differs from original calendar plan')
    return out
