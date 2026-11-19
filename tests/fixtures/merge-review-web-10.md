# Merge review: web#10 — STORY-106 Costs page

## Findings
**[P1] src/pages.py:30 — Home and Costs show different totals for the same month.**
**[P2] src/money.py:12 — Negative amounts render as "$-5.-50".**

### Delta checked against my findings
| Finding | Status | Evidence |
| **P1 Home and Costs show different totals** | **Fixed** | `pages.py:30-44` now uses the same total function as the Costs page; a test renders both pages and compares them. |
| P2 negative amount rendering | **Fixed** | `money.py:12-20` formats the absolute value and adds the sign once; tests cover -5.5, -0.005 and -0.004. |
