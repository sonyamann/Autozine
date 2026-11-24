"""Tag corpus snippets with a quiz theme, using Jev.

Writes corpus/theme_tags.csv (id, theme, secondary_theme), which build_snippets.py
merges into snippets.csv. Answers are cached, so re-running only pays for new snippets.

Run from the project root: python3 corpus/tag_themes.py
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'app'))
import jev  # noqa: E402

OPTIONS = dict(jev.THEMES, general='Human nature, morals, time, fate, or anything that fits none of the others')
SKIP_KINDS = ('highlight', 'tarot', 'flower')      # already tagged, not themed, or always nature
BATCH = 80


def main():
    with open(os.path.join(HERE, 'snippets.csv'), encoding='utf8') as fh:
        rows = [r for r in csv.DictReader(fh)]
    tags = {r['id']: ('nature', '') for r in rows if r['kind'] == 'flower'}
    todo = [r for r in rows if r['kind'] not in SKIP_KINDS]
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        state = {'passages': {r['id']: (r['key'] + ': ' if r['kind'] == 'portent' else '')
                              + ' '.join(r['text'].split()[:60]) for r in batch}}
        questions = {r['id']: {'type': 'choice', 'criteria': OPTIONS,
                               'instructions': f"Which subject area does the passage in `passages.{r['id']}` "
                                               'most belong to?'} for r in batch}
        for i, a in jev.ask(state, questions).items():
            ranked = sorted(a['probabilities'].items(), key=lambda kv: -kv[1])
            second = ranked[1][0] if ranked[1][1] >= 0.25 and ranked[1][0] != 'general' else ''
            tags[i] = (ranked[0][0], second)
        print(f'{min(start + BATCH, len(todo))}/{len(todo)}', end='\r', flush=True)
    with open(os.path.join(HERE, 'theme_tags.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'theme', 'secondary_theme'])
        for i in sorted(tags):
            w.writerow([i, *tags[i]])
    print(f'\n{len(tags)} snippets tagged; {jev.usage["input_tokens"]:,} new input tokens')


if __name__ == '__main__':
    main()
