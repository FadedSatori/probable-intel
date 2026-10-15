# /new-node — Scaffold a new node type end-to-end

Add a new node type to the probable-intel system. Takes two arguments: `TypeName` (PascalCase) and `tier` (harvesters|analysts|sentinels|archivists|coordinators|counterintel).

Example: `/new-node DarkWebNode harvesters`

## Steps to execute

1. **Create the node file** at `probable_intel/nodes/<tier>/<snake_name>_node.py`
   - Subclass `BaseNode`
   - Implement `setup()`, `run()`, `teardown()`
   - Use `_wait_any(self._subscriptions)` for subscriber nodes
   - Use `_cb_check/success/failure(url)` in any fetch loop
   - Emit via `self.emit(self._emit_channel, packet)`
   - Look at an existing node in the same tier as template

2. **Register in factory** — `probable_intel/hub/factory.py` → add to `_lazy_imports()` dict:
   ```python
   "<TypeName>": lambda: __import__('...nodes.<tier>.<snake>_node', fromlist=['<TypeName>']).<TypeName>,
   ```

3. **Add to valid types** — `probable_intel/nexus/parser.py` → add `"<TypeName>"` to `_VALID_NODE_TYPES` set

4. **Create the test file** at `tests/unit/nodes/test_<snake_name>_node.py`
   - Import node class and `NodeSpec`, `EmitSpec`, `Spine`
   - Write `_make_spec()` helper
   - Write at minimum: setup test, emit test, error-handling test

5. **Update CLAUDE.md** — add node to the "Implemented node catalog" table

6. **Update ROADMAP.md** — move node from Sprint Queue to Completed if it was listed

7. **Run verification**:
   ```bash
   python -m pytest tests/unit/nodes/test_<snake_name>_node.py -v
   pi validate nexus/apparatuses/mvp-demo.nx
   ```

## Key invariants (never violate)
- Nodes only communicate via `Spine.publish()` / subscriptions — never direct calls
- `source_hash` set once at creation, preserved through all `relay()` hops
- `SecretManager` is the only place that reads `.env`
- `_emit_channel` and `_emit_priority` are already set by `BaseNode.__init__` — don't re-declare them
