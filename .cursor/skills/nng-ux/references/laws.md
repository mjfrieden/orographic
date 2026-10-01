# Cognitive and interaction laws (NN/g canon)

Use these when a heuristic score is tied but the user is still slow, lost, or overloaded. NN/g teaches these as practical UX laws, not as decoration.

## Jakob’s Law

People expect your site to work like other sites they already use.

- Auth, search, logout, and password-reveal should not require a new mental model.
- Break convention only when the product’s overlay demands a diegetic skin **and** the function remains obvious.

## Fitts’s Law

Time to a target is a function of distance and size.

- Primary actions ≥ 40×40 CSS pixels on touch. Do not rely on 24px icon-only hits for money or auth.
- Related actions sit together (refresh next to its status; submit next to the form).
- Do not place logout on top of a dense well, or a preview button on a moving foil.

## Hick’s Law

Choice time grows with the number and obscurity of choices.

- Default the common path. Hide rare paths.
- Do not present four equally loud buttons when one is the task.
- Labels must be discriminable (`Positions` vs `Orders`, not two gold gates that both say “Open”).

## Miller’s Law / working memory

People hold about 7±2 chunks — often fewer under stress.

- Group related fields. Do not make the user remember a number from the top of the page to use it at the bottom.
- Status, amount, and action should share a glance.

## Aesthetic-usability effect

A handsome UI is judged easier to use — until it hides a failure.

- Visual polish cannot substitute for H1/H9. A gold error that does not say what to do still fails.
- Conversely, do not “fix” usability by stripping a coherent design language the overlay requires.

## Peak-end rule

People remember the peak intensity and the ending.

- Empty, error, and success endings are part of the product. Do not leave them as unstyled dumps.
- The last step of a money path (preview → confirm → done) must be the clearest step.

## Progressive disclosure

Show what is needed now; reveal complexity on request.

- Research, raw tables, hashes, and expert logs belong behind details/drawers.
- The disclosed panel must still be usable at 720px.

## Interaction cost

Every glance, click, scroll, and wait is a cost.

- If a daily user needs more than one click to refresh the thing they came to see, cut a click.
- Auto-focus and skip links reduce cost for keyboard users.
- Do not add a modal to a problem that a sentence would fix.

## Information scent

Links and tabs must smell like their destination.

- Tab labels match panel titles.
- “Open research details” must open research details, not a different product.

## Cognitive load

Cut **extraneous** load (noise, duplicate labels, overlapping chrome). Keep **germane** load (the actual decision). Never dump **intrinsic** domain complexity into 9px type to make it “fit.”
