"""Translate application metadata only; provider/user content is never rewritten."""
import json
import re
from functools import lru_cache
from pathlib import Path


def language(header=None):
    if header is None:
        return 'tr'  # Legacy API clients without a language header keep the original contract.
    return 'tr' if header.split(',')[0].strip().lower().startswith('tr') else 'en'


@lru_cache(maxsize=1)
def catalog():
    return json.loads((Path(__file__).resolve().parent.parent / 'static' / 'locales' / 'en.json').read_text())


def translate(text, lang='tr', error=False):
    if lang != 'en' or not isinstance(text, str):
        return text
    found = catalog().get(' '.join(text.split()))
    if found:
        return found
    rules = [(r'^Sağlayıcı HTTP (\d+) döndürdü$',r'Provider returned HTTP \1'),
             (r'^Dosya boş veya (\d+) MB dosya yükleme sınırını aşıyor$',r'File is empty or exceeds the \1 MB upload limit')]
    for pattern, replacement in rules:
        if re.match(pattern,text):
            return re.sub(pattern,replacement,text)
    return 'Could not complete the operation. Check your input, permissions, or connection.' if error else text
