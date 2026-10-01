# Severity (Nielsen)

Assign one number per finding. Ship the highest number you can finish in this pass.

| Level | Name | Meaning | Typical ship? |
| --- | --- | --- | --- |
| 0 | Not a problem | Reviewer preference only | No |
| 1 | Cosmetic | Unlikely to slow a task | Only if the pass is explicitly polish |
| 2 | Minor | Some users hesitate; workaround is obvious | Yes, if nothing ≥3 remains on this surface |
| 3 | Major | Users fail or take a long detour; they may recover | **Yes — default target** |
| 4 | Catastrophe | Task failure, data loss, money sent by mistake, cannot sign in | **Must fix now; stop other work** |

## How to pick a number

Ask, in order:

1. Can a new user complete the task without help?
2. If they fail, do they know what happened and what to try?
3. How often does this path occur (every session vs rare settings)?
4. Is money, identity, or irrecoverable state involved?

Frequency × impact raises severity. A confusing label on a daily dashboard is often a 3. The same label on a yearly admin hash is a 2.

## Do not inflate

- “I would have used a different font” is 0–1.
- “This overlaps the only logout control on a phone” is 3–4.
- “The error is a raw exception next to no retry” is 3, 4 if it blocks trading or login.
