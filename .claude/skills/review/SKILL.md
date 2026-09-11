---
name: review
description: Weekly coaching review of the tradedesk journal and paper book - what went well, what hurt, rule breaks, one change for next week.
---

Use the `tradedesk` MCP tools (read-only):

1. Call `journal_stats` for paper vs live expectancy, per-setup track records (note any
   benched setup) and rule adherence.
2. If a written review exists for this week under data/reviews/, read it; otherwise run
   `uv run tradedesk review week` and read the file it writes.
3. Summarise for the user: the numbers first (computed by code), then the coaching points,
   then exactly ONE process change to make next week. Do not suggest new setups, levels or
   sizes. Flag a live-vs-paper gap worse than -0.2R as the priority.
