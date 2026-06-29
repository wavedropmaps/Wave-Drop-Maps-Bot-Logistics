---
name: likec4
description: Model and visualize software architecture as code with LikeC4, then launch the live preview. Use this skill WHENEVER the user wants to diagram, map, or document how a system fits together — especially if they mention LikeC4, the C4 model, an architecture diagram, how the pieces connect, drawing or showing the architecture, container or component diagrams, or want a picture of how services, databases, APIs, bots, queues, or websites relate. Also trigger when the user asks to write a .c4 or .likec4 model, or to run npx likec4 start. The skill enforces correct LikeC4 DSL syntax so the model parses and the diagram renders on the first try, and it drives the full flow from reading the real project, to writing the model, to previewing it.
---

# LikeC4 — architecture as code

LikeC4 describes a system's architecture in a small text DSL and renders interactive C4 diagrams from it. The #1 failure mode is **getting the DSL syntax subtly wrong** so the model won't parse — this skill exists to prevent that. Always follow the workflow and copy the syntax patterns below exactly.

## Workflow — always in this order

1. **Read the REAL project first. Never invent the architecture.**
   Open the actual code/config and find the true pieces and how they connect. **Model conceptually, not literally** — don't create separate boxes for tiny internal config files (like `.mcp.json` or `skills.json`) unless explicitly requested. Map the high-level human understanding of how the systems connect and flow.

2. **Write the model** in a `.c4` file inside `ai-hub/docs/architecture/` (e.g. `ai-hub/docs/architecture/my-system.c4`). Do NOT put it in the repo root. Use the three blocks: `specification` → `model` → `views`. Match the syntax reference below character-for-character.

3. **Preview it.** Run the dev server so the user sees the diagram:
   ```bash
   npx likec4 start
   ```
   It recursively finds every `*.c4` / `*.likec4` file and serves an interactive diagram with hot-reload. Tell the user the local URL it prints. 
   ⚠️ **Frozen Server Warning:** If you edit the `.c4` file later but the user says the diagram hasn't updated, the dev server's file watcher has frozen. Do NOT assume the user is wrong. Immediately use your task management tools to kill the background task and restart it.

4. **Validate if anything looks off:** `npx likec4 validate` reports parse/model errors with line numbers.

## File rules

- Extension MUST be `.c4` or `.likec4`. Nothing else is picked up.
- A file has up to three top-level blocks, in this order: `specification { }`, `model { }`, `views { }`. (Also allowed at top level: `import`, `deployment`, `global`.)
- You can split across multiple files — all `.c4`/`.likec4` files merge into one model. For a first diagram, one file is simplest.

---

## Syntax reference (copy these patterns exactly)

### 1. `specification` — define your vocabulary FIRST
Every element kind and relationship kind you use in `model` must be declared here first, or it won't parse.

```c4
specification {
  // element kinds — these are the "types" of boxes
  element actor
  element system
  element service
  element database
  element queue

  // a kind can carry default styling
  element database {
    style {
      shape cylinder
    }
  }

  // relationship kinds add meaning to arrows (optional)
  relationship async {
    line dotted
    color amber
  }

  // tags for filtering/marking (optional)
  tag deprecated
}
```

Useful `shape` values: `rectangle` (default), `cylinder` (databases), `queue`, `person` (actors), `browser`, `mobile`, `storage`.

### 2. `model` — the actual elements and how they connect

**Define an element** as `kind name 'Title'` (or `name = kind 'Title'`):
```c4
model {
  customer = actor 'Staff member'
  bot      = service 'Discord Bot' 'Pushes JSON every few hours'
  repo     = system 'GitHub Repo'
  site     = system 'Staff Hub Website'

  db = database 'Postgres' {
    technology 'PostgreSQL 15'
    description 'Stores everything'
  }
}
```
- 1st string after the kind = **title**, optional 2nd string = **description**.
- Tags go FIRST inside the body: `#deprecated, #team1`.

**Nest elements** to show containment (a system contains services, a service contains components):
```c4
model {
  cloud = system 'Cloud Platform' {
    api = service 'API'
    worker = service 'Worker'
  }
}
```
Nested elements are referenced by dotted path: `cloud.api`, `cloud.worker`.

**Relationships** use `->`. Define them anywhere in `model` (top level, or nested inside a parent):
```c4
model {
  // basic
  bot -> repo

  // with a label
  bot -> repo 'pushes JSON files every few hours'

  // typed (kind must be declared in specification)
  bot -[async]-> repo

  // with full properties
  site -> repo 'reads JSON' {
    technology 'HTTPS / GitHub raw'
    #team1
  }
}
```

### 3. `views` — the diagrams to render

```c4
views {
  // the default landing view — show everything
  view index {
    title 'System Landscape'
    include *
    autoLayout LeftRight
  }

  // a focused view scoped to one element and its insides
  view of cloud {
    title 'Cloud — internals'
    include *
    autoLayout TopBottom
  }
}
```
- `include *` = include everything in scope. `include <name>` = add a specific element. `exclude <name>` = drop one.
- `include * -> service` = include relationships pointing at `service`.
- `view of <element>` scopes the view to that element + its children.
- `autoLayout` directions: `TopBottom`, `BottomTop`, `LeftRight`, `RightLeft`.
- Styling: `style <selector> { color muted; shape rectangle }` (e.g. `style * { color muted }`).

---

## Complete worked example

This models a Discord bot that pushes JSON to a GitHub repo, which a website reads — a full, parseable file:

```c4
specification {
  element actor   { style { shape person } }
  element service
  element system
  element database { style { shape cylinder } }
}

model {
  staff = actor 'Staff member' 'Views the leaderboards'

  bot = service 'Discord Bot' 'discord.py bot' {
    technology 'Python'
    description 'Builds JSON payloads and pushes them on a schedule'
  }

  db = database 'bot_database.db' {
    technology 'SQLite'
  }

  repo = system 'GitHub Repo' 'Stores the JSON data files'

  site = system 'Staff Hub Website' {
    technology 'GitHub Pages'
    description 'Reads the JSON and renders leaderboards'
  }

  bot  -> db   'reads/writes state'
  bot  -> repo 'pushes JSON files every few hours'
  site -> repo 'fetches the JSON on page load'
  staff -> site 'opens in browser'
}

views {
  view index {
    title 'Wave Staff Hub — how the pieces connect'
    include *
    autoLayout LeftRight
  }
}
```

Save as `ai-hub/docs/architecture/architecture.c4`, then `npx likec4 start`.

---

## Common mistakes that break parsing (avoid these)

- **Using an element/relationship kind that wasn't declared** in `specification`. Declare it first.
- **Wrong arrow:** it's `->`, not `-->` or `→`. Typed is `-[kind]->`.
- **Wrong file extension** — must be `.c4` or `.likec4`.
- **Tags not first** inside an element body — `#tag` lines must precede other properties.
- **Quoting:** titles/descriptions use single quotes `'...'`. Multi-line descriptions use triple quotes `"""..."""`.
- **Referencing a nested element by short name** — use the dotted path (`cloud.api`, not `api`) from outside its parent.
- Don't hand-place boxes — let `autoLayout` do it; only add manual layout once the content is right.

## CLI cheat-sheet

```bash
npx likec4 start                 # live preview server (primary command)
npx likec4 validate              # check the model parses
npx likec4 export png -o ./out   # PNG (needs Playwright)
npx likec4 build -o ./dist       # static website
npx likec4 gen mermaid           # export to mermaid / dot / d2 / plantuml / react
npx likec4 format                # auto-format .c4 files
```

Reference: the LikeC4 project and docs — https://github.com/likec4/likec4 and https://likec4.dev/dsl/intro/
