# Tweet labels

`tweets-to-label.csv` holds 200 real tweets from the test accounts, in random order, for setting Jev's thresholds.
Fill in two columns with `y` or `n`:

- **too_raw:** is this tweet about grief, a death, serious illness, violence, abuse or a personal crisis, so that
  a joking definition or quote printed beside it would be hurtful? Dark humor and ordinary complaints are `n`.
- **stands_alone:** would a stranger understand and enjoy this tweet printed on its own, with no link, image,
  thread or news event for context?

`jev-scores.csv` has Jev's scores for the same rows. Don't look at it until you've finished labelling.

This folder is not in git, because it contains other people's tweets.
