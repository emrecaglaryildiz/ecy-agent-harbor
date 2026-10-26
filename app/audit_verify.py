"""Offline streaming audit verifier. An independent checkpoint anchors a known prefix."""
import argparse
import json
from .audit import FIELDS,ZERO,digest


def verify(path,anchor=None):
    expected=None;anchor_matched=False
    if anchor:
        with open(anchor,'rb') as source:raw=source.read(65537)
        if len(raw)>65536:raise ValueError('Invalid checkpoint')
        expected=json.loads(raw)
        if not isinstance(expected,dict) or type(expected.get('seq')) is not int or expected['seq']<0:raise ValueError('Invalid checkpoint')
        anchor_matched=expected['seq']==0 and expected.get('hash')==ZERO
    with open(path,'rb') as source:
        first=source.readline(65537)
        if len(first)>65536:raise ValueError('Invalid audit header')
        head=json.loads(first)
        if not isinstance(head,dict) or head.get('type')!='checkpoint' or head.get('format')!=1 or head.get('algorithm')!='sha256-canonical-json-v1' or type(head.get('seq')) is not int or head['seq']<0:raise ValueError('Invalid audit header')
        if expected and any(expected.get(k)!=head.get(k) for k in ('format','algorithm','stream_id')):raise ValueError('Independent checkpoint mismatch')
        seq=0;previous=ZERO
        while True:
            line=source.readline(65537)
            if not line:break
            if len(line)>65536:raise ValueError('Invalid audit record')
            row=json.loads(line)
            if not isinstance(row,dict) or set(row)!=set(FIELDS)|{'hash','type'} or row['type']!='event' or type(row['seq']) is not int or row['seq']!=seq+1 or row['stream_id']!=head['stream_id'] or row['previous_hash']!=previous or row['hash']!=digest(row):raise ValueError('Audit chain mismatch')
            seq=row['seq'];previous=row['hash']
            if expected and seq==expected['seq']:anchor_matched=previous==expected.get('hash')
    if seq!=head['seq'] or previous!=head['hash']:raise ValueError('Audit export is incomplete')
    if expected and not anchor_matched:raise ValueError('Independent checkpoint mismatch')
    return seq


def main():
    parser=argparse.ArgumentParser();parser.add_argument('export');parser.add_argument('--checkpoint');args=parser.parse_args()
    try:count=verify(args.export,args.checkpoint)
    except (OSError,ValueError,TypeError,KeyError):raise SystemExit('INVALID: audit export or checkpoint did not verify') from None
    print('VALID:',count,'events;', 'independent checkpoint prefix matched' if args.checkpoint else 'local consistency only; no independent anchor')

if __name__=='__main__':main()
