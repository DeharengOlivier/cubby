# Cubby

> Tidy your Downloads folder automatically. Every file finds its cubby.

Cubby is a small, dependency-light CLI that watches a folder (your `~/Downloads`
by default) and files each new download into the right place using a
**three-stage cascade**: it looks at the **filename** first, peeks at the
**content** when the name is uninformative, and falls back to the **file type**
last. Whatever it cannot confidently place lands in a single `_Unsorted` folder
so your root stays clean.

```
~/Downloads/
├── Invoices/
├── Bank-Statements/
├── Legal/
├── Resumes/
├── Images/
├── Music/
└── _Unsorted/
```

Status: work in progress. See [`docs/`](docs/) for details.
