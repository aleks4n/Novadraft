# dev/ — agent test & development harness

Tiny CLI to invoke individual agents or multi-agent flows during development.
Every run is logged to `dev/runs/{timestamp}_{name}.json` (inputs, output,
error, duration) so you can compare iterations without setting anything up.

## Usage

```bash
cd code/novadraft/dev

# see what's registered
python run.py list

# run an agent with inline inputs
python run.py agent chat --kwargs '{"prompt": "hi"}'

# run with a fixture
python run.py agent paper   --fixture sample_paper.json
python run.py flow  paper_then_survey --fixture demo_flow.json
```

## Adding things

- **New agent / flow**: register a callable in `registry.py` (`AGENTS` or `FLOWS`).
- **New fixture**: drop a JSON file into `fixtures/`; keys must match the
  callable's kwargs.

## Files

- `run.py` — CLI runner + auto-logger
- `registry.py` — central map of agents and flows
- `fixtures/` — reusable input payloads
- `runs/` — auto-generated run logs (gitignore-worthy)
