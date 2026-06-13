# Recipes

Small, copy-pasteable configurations for common needs.

## Sort a different folder

```toml
[settings]
source = "~/Desktop"
```

Or one-off: `cubby run --source ~/Desktop`.

## Route a project by codename, with a content safety net

```toml
[[category]]
name = "Project-Atlas"
name_patterns = ["atlas", "\\batl-\\d+\\b"]
content_patterns = ["project atlas", "atlas initiative"]
```

The content hint rescues exports whose filename is just an id.

## Keep screenshots separate from other images

List the more specific category first:

```toml
[[category]]
name = "Screenshots"
name_patterns = ["screenshot", "capture d.écran", "screen shot"]

[[category]]
name = "Images"
extensions = ["png", "jpg", "jpeg", "gif", "heic"]
strong_ext = true
```

## Drop duplicate downloads automatically

```toml
[settings]
dedupe = true
```

A byte-identical `report.pdf` downloaded twice is removed instead of becoming
`report (1).pdf`.

## Sort instantly, no delay

```sh
cubby run --delay 0
```

## Preview as JSON for scripting

```sh
cubby plan --json | jq '.items[] | select(.category == "Invoices") | .name'
```
