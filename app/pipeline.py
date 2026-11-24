"""Tweet processing: filter, tag, rank, diversify (SPEC "Tweet processing", steps 1-8).

Everything here is rule-based, so it runs with no API keys. When a TypeSafe key
is set, make_spread.py adds Jev's tags on top (high emotion, stands alone); the
PII patterns below are still a stand-in for Presidio. Labels are saved with the
seed in record.json.
"""
import html
import re
import statistics

STOP = set("""a about after all also am an and any are as at be because been but by can did do does for from get got had
has have he her here him his how i i'd i'll i'm i've if in into is it it's its just like me more most my no not now of on
one only or our out over she so some than that the their them then there these they this to too up us very was we were
what when where which who why will with would you your yours really still even never ever every much many been being
don't can't won't didn't isn't that's there's today tonight day days thing things""".split())

# Step 4. Stand-in for Presidio.
PII = [re.compile(p, re.I) for p in (
    r'\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b',            # phone
    r'\b[\w.+-]+@[\w-]+\.[\w.]+\b',                                    # email
    r'\b\d{1,5} [A-Z][a-z]+ (?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Ln|Drive|Dr)\b',  # street address
)]
# Step 5. Keyword fallback for the high-emotion tag; Jev's tag is added in make_spread.py.
HIGH_EMOTION = re.compile(
    r'\b(died|dies|dying|death|dead|funeral|grief|grieving|mourning|passed away|rip|cancer|chemo|hospice|hospital|'
    r'diagnos\w+|miscarriage|suicid\w+|overdose|shooting|killed|murder\w*|assault\w*|abuse\w*|war|bombing)\b', re.I)
URL = re.compile(r'https?://\S+')
MENTION = re.compile(r'@\w+')
THREAD = re.compile(r'^\s*(\d+/\d*|\d+\.)\s|^\s*\(?cont|🧵')

MAX_CHARS = 280          # spec: exact cutoff TK
MIN_WORDS = 4


def words(text):
    text = MENTION.sub(' ', URL.sub(' ', text.lower()))
    return [w.strip("'") for w in re.findall(r"[a-z][a-z']+", text)]


def content_words(text):
    return [w for w in words(text) if w not in STOP and len(w) > 2]


def engagement(t):
    return t.get('likeCount', 0) + 2 * t.get('retweetCount', 0) + 2 * t.get('quoteCount', 0) + t.get('replyCount', 0)


def process(tweets, allow_context=False):
    """Return (kept, dropped). Each kept tweet gains 'score', 'labels' and 'tokens'."""
    kept, dropped = [], []
    for t in tweets:
        t = dict(t, text=html.unescape(t['text']))     # the API returns &amp;, &lt; and &gt; escaped
        text = t['text']
        bare = MENTION.sub('', URL.sub('', text)).strip()
        n_words = len(text.split())
        reason = None
        if t.get('retweeted_tweet') or text.startswith('RT @'):
            reason = 'retweet'                                         # step 1
        elif t.get('isReply'):
            reason = 'reply'
        elif not bare:
            reason = 'link or mentions only'                           # step 2
        elif len(bare.split()) < MIN_WORDS:
            reason = 'too short'
        elif len(MENTION.findall(text)) * 2 > n_words:
            reason = 'mostly mentions'
        elif THREAD.search(text):
            reason = 'thread fragment'
        context = ('quote tweet' if t.get('quoted_tweet') else
                   'has an image or video' if (t.get('extendedEntities') or {}).get('media') else None)
        if reason:
            pass
        elif context and not allow_context:
            reason = context
        elif len(text) > MAX_CHARS:
            reason = 'too long'                                        # step 3
        elif any(p.search(text) for p in PII):
            reason = 'possible personal information'                   # step 4
        if reason:
            dropped.append((t, reason))
            continue
        t['labels'] = {'high_emotion': bool(HIGH_EMOTION.search(text))}   # step 5
        if context:
            t['labels']['context'] = context      # kept only for thin accounts; needs vetting
        t['tokens'] = content_words(text)                                 # step 6
        t['display'] = URL.sub('', text).strip()
        kept.append(t)

    if not kept:
        return kept, dropped
    median = statistics.median(engagement(t) for t in kept) or 1          # step 7
    for t in kept:
        t['score'] = engagement(t) / median
    kept.sort(key=lambda t: (-t['score'], t['id']))
    return kept, dropped


def similarity(a, b):
    a, b = set(a['tokens']), set(b['tokens'])
    return len(a & b) / len(a | b) if a and b else 0.0


def pick_diverse(ranked, n, taken=(), penalty=3.0, where=None):
    """Step 8: greedy pick, penalizing similarity to tweets already chosen."""
    chosen, taken = [], list(taken)
    pool = [t for t in ranked if t['id'] not in {x['id'] for x in taken} and (where is None or where(t))]
    while pool and len(chosen) < n:
        best = max(pool, key=lambda t: (t['score'] - penalty * max(
            [similarity(t, c) for c in chosen + taken] or [0]), t['id']))
        chosen.append(best)
        pool.remove(best)
    return chosen
