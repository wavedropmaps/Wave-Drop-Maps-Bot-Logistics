# Automation Tree Decision Architecture
> Referenced from `AGENTS.md` → Codebase Map. The node-graph engine (`automation_tree.py` + `model_nodes.py` + `automation_handlers.py`) that drives a proof image through chained ML models to a single terminal verdict.

### Why a tree (vs. older monolithic management)
- The cascade is expressed as a directed graph of decision nodes rather than one giant if/else block or a single multi-class model. Each node owns exactly one ML model and one small `route()` policy, so models are composed by routing between them instead of being managed in one place.
- This makes the cascade declarative: adding/replacing a model means editing one node and its routing edges, not the orchestrator. The orchestrator (`AutomationTree.process_image`) is model-agnostic and simply follows `ROUTE` edges until a terminal action appears.

### The Decision object (`utils/automation_tree.py:9`)
- `Decision(action, next_node, message_key, roles_to_grant, confidence, class_name, failed_node)`.
- `action` is the verdict type: `'ROUTE'` (continue traversal), or terminal `'GRANT_*'` / `'REJECT_*'` / `'HITL'`.
- `next_node` names the successor node when `action == 'ROUTE'`. `confidence`, `class_name`, and `failed_node` carry the model output forward for logging and for the HITL embed.

### Node structure
- `AutomationNode` (base, `automation_tree.py:25`) lazily loads its YOLO model on first `evaluate()` and runs on GPU if available, else CPU.
- `YOLOAutomationNode` (`model_nodes.py:11`) implements `evaluate()`: it runs `model(image_path, verbose=False)`, extracts the top-1 class index, its confidence (`top1conf`), and the class name (`names[top1]`), then delegates to `self.route(class_name, confidence, guild_config)`.
- `ViTAutomationNode` (`model_nodes.py:128`) is the transformer variant: loads from safetensors with key-remapping for older ViT layer names, resizes input to 224×224, normalizes to [-1, 1], then calls `route()` the same way. Only Model3b uses it.
- Each concrete model subclasses one of these and implements only `route()` — a confidence gate plus a class→action mapping. Models therefore "plug in" purely by being a node with a `route()` policy; the engine never special-cases any individual model.

### How models map class → action (`utils/model_nodes.py`)
- **Model1_Gatekeeper** (`:28`): `<0.99 → HITL`; `Garbage→REJECT_DYNAMIC`, `invite→REJECT_INVITE`, `Twitter→ROUTE Model2_TwitterRouter` (if twitter enabled), `Creator Code→ROUTE Model5_UIRouter`, else `HITL`.
- **Model2_TwitterRouter** (`:46`): `<0.99 → HITL`; `desktop→ROUTE Model4_DesktopCheck`, `mobile→ROUTE Model3a_MobileCheck1`, else `HITL`.
- **Model3a_MobileCheck1** (`:58`): `<0.99 → HITL`; `Following only→REJECT_FOLLOWING_ONLY`, `either→ROUTE Model3b_MobileCheck2`, else `HITL`.
- **Model3b_MobileCheck2** (ViT, `:186`): `<0.70 → HITL`; `Following only→REJECT_FOLLOWING_ONLY`, `Liking only→REJECT_LIKING_ONLY`, `scam→REJECT_DYNAMIC`, `Following and liking→GRANT_LEVEL_1`, else `HITL`.
- **Model4_DesktopCheck** (`:70`): `<0.99 → HITL`; same Following/Liking/scam mapping as 3b, `Following and liking→GRANT_LEVEL_1`.
- **Model5_UIRouter** (`:86`): `<0.99 → HITL`; `Online fort website` or `Iphone Shop→GRANT_LEVEL_2` (instant), `Taken via phone→ROUTE Model6_PhonePhoto`, `ScreenShot→ROUTE Model7_Screenshot`, else `HITL`.
- **Model6_PhonePhoto** (`:100`): `<0.99 → HITL`; `Press search→REJECT_PRESS_SEARCH`, `zoom out→REJECT_ZOOM_OUT`, `using code correctly→GRANT_LEVEL_2`, else `HITL`.
- **Model7_Screenshot** (`:114`): `<0.99 → HITL`; `Need to press search→REJECT_PRESS_SEARCH`, `Zoom out→REJECT_ZOOM_OUT`, `Correctly using code→GRANT_LEVEL_2`, else `HITL`.

### Traversal / evaluation (`process_image`, `automation_tree.py:53`)
- `process_image(image_path, start_node_name, guild_config, force_class=None)` walks `current_node_name` through `self.nodes`.
- Loop (`:60`): if the node name is missing → `Decision('HITL')`; otherwise it calls `node.evaluate(image_path, guild_config)` (or, if `force_class` is set on the start node, `node.route(force_class, 1.0, ...)` to bypass inference — used when staff manually re-route from HITL).
- Any exception during evaluation is caught and converted to `Decision('HITL', failed_node=...)` (`:73`) — fail-safe to human review, never auto-action on error.
- If `action == 'ROUTE'`, it follows `decision.next_node`; any other action is **terminal** and is returned immediately, stamping `failed_node = current_node_name`. A fully-walked path with no terminal falls back to `HITL`.

### Handlers (`utils/automation_handlers.py`)
- `DynamicHandlers` turns abstract reject actions into localized, user-facing replies. It detects the user's language from role membership and the target method (Invite vs Twitter) from `INVITE_ROLE_ID = 1210764376676634684` / `TWITTER_ROLE_ID = 1210764292719247400`.
- `GARBAGE_MAPPINGS` (`:26`) is a 14-entry table keyed by `(language, target_method)` across 7 languages × 2 methods; `get_dynamic_garbage_reply()` (`:57`) formats `{mention}` + the right help-channel ID, falling back to English/Twitter.
- `get_invite_rejection()` (`:81`) points users at a configured Support role; `get_loot_routes_garbage_reply()` (`:93`) is the Server-2 loot-route variant that routes by role to a different channel. These handlers are what the terminal `REJECT_*` Decisions resolve to before the bot replies.
