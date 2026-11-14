# Phase 1 - Situation interview

Goal: know enough to fill the plan-parameters `tax`, `coverage_tier`, `current_plan` and plan list, and to plan the claims-data work. Ask in batches of 2-4 questions. For each question, say in one line why it matters. Always offer "I don't know"; when chosen, give the "where to find it" hint and move on with a labelled assumption.

## Batch 1 - Who and what

| Ask | Why it matters | If they don't know |
|---|---|---|
| Who will be on the plan next year? (just you / you + spouse or partner / you + children / family) and how many people | Sets the premium tier, the family deductible and OOP rules, and the HSA limit | Their current enrollment is in the employer benefits portal |
| Which plan are you on now, and who is the insurer? | The current plan's rules let us check the model against what they really paid | Insurance card, benefits portal, or the insurer's member site |
| Which plans can you pick for next year? Is at least one an HSA plan / HDHP? | These become the options compared | Open-enrollment guide or benefits portal; an HDHP is usually labelled "HSA plan" or "high-deductible" |
| When is the enrollment deadline? | Sets how much time there is to gather documents; skip optional steps if short | Benefits portal / HR email |

If they have no HSA-eligible option, say so: there is nothing to decide about an HSA. The toolkit can still compare two non-HSA plans if they want (set `current_plan`).

## Batch 2 - Money and tax

| Ask | Why | Where to find it |
|---|---|---|
| How often are you paid? (weekly 52, every two weeks 26, twice a month 24, monthly 12) | Premiums are usually quoted per paycheck | A pay stub |
| Federal tax bracket (10/12/22/24/32/35/37%) | HSA contributions and pre-tax premiums save this rate | Last year's return: taxable income on Form 1040, then the IRS bracket table for their filing status |
| State income-tax rate | Same | State revenue department rate table; enter 0 for states without income tax, and for California or New Jersey (they tax HSA contributions) |
| Would HSA contributions go through payroll? (almost always yes at employers) | Payroll contributions also save FICA (7.65%, or 1.45% above the Social Security wage base) | Benefits guide |
| How much could you put into the HSA each year? (default: the legal maximum minus employer money) | Drives tax savings and cash flow | Their budget; the model lets them change it later |
| Will you be HSA-eligible for the whole plan year? (starting mid-year, Medicare starting, or losing eligibility changes the limit) | The limit is prorated by eligible months (the "last-month rule" has a testing period); pass a lower `--employee-hsa` | IRS Publication 969 |
| Does a spouse have their own HSA or HSA contributions? | Spouses with family HDHP coverage share one family limit | Spouse's pay stub / benefits |
| Will you be 55 or older by Dec 31 of the plan year? | Allows an extra catch-up contribution | - |

## Batch 3 - Eligibility checks (ask plainly, explain why)

An HSA can only be funded by someone who, for the months in question:
- is covered by an HSA-qualified HDHP,
- has no other non-HDHP health coverage (including a spouse's PPO that covers them, or TRICARE),
- has no general-purpose health FSA or HRA (theirs **or their spouse's**; a limited-purpose dental/vision FSA, or a post-deductible HRA, is fine),
- is not enrolled in Medicare (watch for anyone turning 65 or already on Social Security; Medicare Part A enrollment can be backdated up to 6 months), and
- cannot be claimed as someone else's tax dependent.

Also ask: **are the covered children (or partner) your tax dependents?** HSA money is tax-free only for the account holder, spouse and tax dependents. Adult children up to 26 can be on the plan without being dependents; their bills then shouldn't be paid from the HSA (they can open their own HSA and contribute up to the family limit while on a family HDHP). This changes cash-flow advice, not which plan is cheaper.

## Batch 4 - What records exist (sets up Phase 3)

Ask what they can get, not what they "should" have:
- Can you log into the insurer's member site? Does it have a claims page with a download/export button? (List medical, dental, pharmacy separately; pharmacy is often a different company, the pharmacy benefit manager.)
- Do you have Explanation of Benefits (EOB) PDFs or paper statements?
- Can you see claims for everyone on the plan? (Adult dependents' claims are usually hidden from the subscriber.)
- Anything planned or recurring next year? Surgery, pregnancy, therapy, a new long-term prescription, braces, orthotics. These go into estimates.
- Year-to-date deductible / out-of-pocket "accumulator" totals (usually on the member site's coverage or benefits page, or printed on recent EOBs). Used for a completeness check.

## Write down

Record answers in `.local/intake_notes.md` (local only): tier, member count, current plan, options, insurer(s), pay periods, tax inputs, eligibility answers, dependent status, data available, planned care, deadline. Don't write names; use "you", "spouse", "child 1".
