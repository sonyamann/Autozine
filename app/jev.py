"""Judgments from TypeSafe's Jev model: tweet tags, theme, and pairing scores.

Every request is cached on disk by its content, so re-rendering a spread costs
nothing and always gets the same answers. Reads TYPESAFE_API_KEY from the environment or .env;
`available()` is False without it and callers fall back to keyword rules.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = 'https://api.typesafe.ai/v1/systemone'
MODEL = 'jev-1.14.0'          # pinned, so cached and fresh answers agree
THEMES = {
    'nature': 'Plants, animals, weather, seasons, landscapes, the outdoors',
    'domestic': 'Home life, food and cooking, family, chores, small everyday comforts',
    'high_techne': 'Technology, science, machines, software, work with tools and systems',
    'cosmopolitan': 'City life, travel, art, fashion, society, culture, ambition',
}
VOICES = {    # type pairings in make_spread.TYPES, described by the voice each suits
    'bookish': 'Reflective, literary, wistful or tender: would read best set like an old novel',
    'machine': 'Dry, technical, deadpan or terse: would read best set like a computer printout',
    'poster': 'Loud, brash, excitable or showy: would read best set like a wanted poster',
}
usage = {'input_tokens': 0, 'requests': 0}


def _key():
    from fetch import env
    return env('TYPESAFE_API_KEY')


def available():
    return _key() is not None


def ask(state, questions):
    """Send one request; return {question id: answer}."""
    body = json.dumps({'model': MODEL, 'state': state, 'questions': questions}, sort_keys=True, ensure_ascii=False)
    cache = os.path.join(ROOT, 'build', 'jev_cache', hashlib.sha256(body.encode()).hexdigest()[:24] + '.json')
    if os.path.exists(cache):
        return json.load(open(cache))
    req = urllib.request.Request(URL, data=body.encode(), headers={
        'Authorization': 'Bearer ' + _key(), 'Content-Type': 'application/json'})
    try:
        out = json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'TypeSafe returned HTTP {e.code}: {e.read().decode()[:300]}')
    usage['input_tokens'] += out.get('usage', {}).get('input_tokens', 0)
    usage['requests'] += 1
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    json.dump(out['answers'], open(cache, 'w'))
    return out['answers']


def tag_tweets(tweets):
    """Return ({tweet id: {'high_emotion', 'standalone', 'attack': p}}, theme, type pairing).

    The type pairing (a key of VOICES) is chosen from the tweets' voice, so the fonts are a surprise
    that still suits the account."""
    state = {'tweets': {f't{i}': t['display'] for i, t in enumerate(tweets)}}
    q = {}
    for i in range(len(tweets)):
        q[f'emotion{i}'] = {'type': 'noul', 'instructions': (
            f'Is the tweet in `tweets.t{i}` about grief, a death, serious illness, violence, abuse, or a personal '
            'crisis, so that joking commentary printed beside it would be hurtful? Dark humor and ordinary '
            'complaints do not count.')}
        q[f'standalone{i}'] = {'type': 'noul', 'instructions': (
            f'Would the tweet in `tweets.t{i}` make sense and be enjoyable to a stranger reading it printed on its '
            'own, with no link, image, thread, or news event for context?')}
        q[f'attack{i}'] = {'type': 'noul', 'instructions': (
            f'Is the tweet in `tweets.t{i}` a hostile personal attack on a named or identifiable person, or '
            'angry political invective? Teasing, self-deprecation and calm disagreement do not count.')}
    q['theme'] = {'type': 'choice', 'instructions': 'Which theme best fits these tweets taken together?',
                  'criteria': THEMES}
    q['voice'] = {'type': 'choice', 'instructions': 'These tweets will be printed in a zine. Which typeface '
                  'best suits the voice and tone of these tweets taken together?', 'criteria': VOICES}
    a = ask(state, q)
    labels = {t['id']: {'high_emotion': a[f'emotion{i}']['noul'], 'standalone': a[f'standalone{i}']['noul'],
                        'attack': a[f'attack{i}']['noul']}
              for i, t in enumerate(tweets)}
    return labels, a['theme']['choice'], a['voice']['choice']


def score(state, items, instructions, levels):
    """Score each item in `items` ({id: backticked path}) from 0 to len(levels)-1."""
    if not items:
        return {}
    q = {k: {'type': 'score', 'instructions': instructions.format(path=path), 'criteria': levels}
         for k, path in items.items()}
    return {k: v['score'] for k, v in ask(state, q).items()}


def score_definitions(matches):
    """matches: [(tweet text, word, definition text)] -> list of scores (0-3)."""
    state = {'pairs': {f'p{i}': {'tweet': t, 'matched_word': w, 'definition': d}
                       for i, (t, w, d) in enumerate(matches)}}
    out = score(state, {f'p{i}': f'pairs.p{i}' for i in range(len(matches))},
                'A satirical dictionary definition will be printed beside a tweet that uses the defined word. '
                'How well does the definition in `{path}` work as a witty aside to the tweet in `{path}`?',
                ['The tweet uses the word in a different sense, or the pairing is confusing or in poor taste',
                 'The word matches but the definition adds nothing to the tweet',
                 'Apt: the definition comments on what the tweet is saying',
                 'Delightful: the definition lands as a sharp or funny remark on the tweet'])
    return [out[f'p{i}'] for i in range(len(matches))]


def score_dreams(matches):
    """matches: [(tweet text, word, dream reading)] -> list of scores (0-3)."""
    state = {'pairs': {f'p{i}': {'tweet': t, 'matched_word': w, 'dream_reading': d}
                       for i, (t, w, d) in enumerate(matches)}}
    out = score(state, {f'p{i}': f'pairs.p{i}' for i in range(len(matches))},
                'A reading from an old dream dictionary (1901) will be printed beside a tweet that uses the '
                'dream\'s keyword, as if foretelling the tweet\'s fate. How well does the reading in `{path}` work as '
                'a playful, mock-solemn aside to the tweet in `{path}`?',
                ['The tweet uses the word in a different sense, or the pairing is confusing or in poor taste',
                 'The word matches but the reading adds nothing to the tweet',
                 'Apt: the reading seems to comment on or predict what the tweet is saying',
                 'Delightful: the omen lands as an uncanny or funny remark on the tweet'])
    return [out[f'p{i}'] for i in range(len(matches))]


def score_general_dreams(tweet_texts, readings):
    """For B-sides with no word match: how well each dream reading suits the page as a whole (0-3)."""
    state = {'tweets': tweet_texts, 'readings': {f'd{i}': x for i, x in enumerate(readings)}}
    out = score(state, {f'd{i}': f'readings.d{i}' for i in range(len(readings))},
                'One reading from an old dream dictionary (1901) will be printed on a zine page made from the '
                'tweets in `tweets`, like a fortune. No tweet uses its keyword. How well does the reading in '
                '`{path}` suit the page as a playful omen?',
                ['Unkind, bleak, or likely to read as a dig at this person',
                 'Harmless but unrelated to anything in the tweets',
                 'Gently fits a subject or mood that runs through the tweets',
                 'Fits the tweets well and would make this person smile'])
    return [out[f'd{i}'] for i in range(len(readings))]


def score_lines(tweet_texts, lines):
    """How well each quotation suits the tweets. lines: [text] -> list of scores (0-3)."""
    state = {'tweets': tweet_texts, 'lines': {f'q{i}': x for i, x in enumerate(lines)}}
    out = score(state, {f'q{i}': f'lines.q{i}' for i in range(len(lines))},
                'One old quotation will be printed on a zine page made from the tweets in `tweets`. '
                'How well does the quotation in `{path}` suit this person\'s voice and preoccupations?',
                ['Unrelated, obscure without context, or jarring beside these tweets',
                 'Generic: a broad remark about life that would suit almost anyone\'s page equally well',
                 'Fits this person: echoes a specific subject or attitude found in these tweets',
                 'Feels chosen for this person: it speaks to something particular in these tweets'])
    return [out[f'q{i}'] for i in range(len(lines))]


def score_images(tweet_texts, descriptions):
    """How well each described picture suits the tweets -> list of scores (0-3)."""
    state = {'tweets': tweet_texts, 'pictures': {f'i{i}': x for i, x in enumerate(descriptions)}}
    out = score(state, {f'i{i}': f'pictures.i{i}' for i in range(len(descriptions))},
                'One vintage picture will illustrate a zine page made from the tweets in `tweets`. '
                'How well does the picture described in `{path}` suit these tweets?',
                ['Nothing to do with the tweets, or an awkward fit',
                 'Neutral: pleasant decoration with no connection',
                 'Shares a subject or mood with the tweets',
                 'Depicts something a tweet mentions, or matches the page strikingly'])
    return [out[f'i{i}'] for i in range(len(descriptions))]


def score_general_definitions(tweet_texts, definitions):
    """For spreads with no word match: how well each definition suits the page as a whole (0-3)."""
    state = {'tweets': tweet_texts, 'definitions': {f'd{i}': x for i, x in enumerate(definitions)}}
    out = score(state, {f'd{i}': f'definitions.d{i}' for i in range(len(definitions))},
                'One entry from a satirical dictionary will be printed on a zine page made from the tweets in '
                '`tweets`. No tweet uses the word. How well does the entry in `{path}` suit the page as a wry, '
                'good-natured aside?',
                ['Unkind, bleak, or likely to read as a dig at this person',
                 'Harmless but unrelated to anything in the tweets',
                 'Gently fits a subject or mood that runs through the tweets',
                 'Fits the tweets well and would make this person smile'])
    return [out[f'd{i}'] for i in range(len(definitions))]
