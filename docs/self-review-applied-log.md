# Self-review applied-change log

Every entry below was written by `self_review/apply.py`, and only after a human approved the
specific `ReviewItem` it corresponds to via the dashboard's Review tab. Each entry names the
git commit that made the change and the evidence file (`data/reviews/applied/<item_id>.json`)
holding the before/after file hashes and the full gauntlet report that justified it - see
[the self-review checkpoint](self-review-checkpoint.md) for the feature's own design and
safety boundary.

Nothing is written here by hand; this file is append-only from `apply()`'s own code.
