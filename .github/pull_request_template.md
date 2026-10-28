## What and why

<!-- Slice id, what it builds, link to its BUILD-LOG entry. -->

## Mini review (PLAN Q8)

Per commit (each commit's diff was reviewed for):

- [ ] Correctness and edge cases
- [ ] Money stays in fixed-point integers (dollars at 10^-6, counts at 10^-2)
- [ ] Reuse: no duplicate of existing code; shared code in the shared module
- [ ] Efficiency on hot paths
- [ ] Simplification
- [ ] Tests cover the change
- [ ] No secrets, keys, real host names, paths or Kalshi data

Per merge (the branch diff against `main`, plus the modules it touches and their callers):

- [ ] Consistent across the slice
- [ ] No drift from the PLAN decisions
- [ ] Docs and guides current; the BUILD-LOG entry has its **Review** section
- [ ] Each finding fixed here or parked in PLAN §9 with its reason
