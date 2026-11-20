# Contributing to ORCAdesk

Thanks for taking a look. ORCAdesk is a small project, so the most useful
contributions are often the simplest: a clear bug report, an ORCA output that
reads wrongly, or a note on where the workflow takes too many clicks.

## Reporting a bug

Open an [issue](https://github.com/wtbdkim/OrcaDesk/issues/new/choose) with:

- the ORCAdesk version (in the top bar, or **Settings → About**), your OS, and
  your ORCA version;
- what you did, what you expected, and what happened instead;
- a screenshot if it is something you can see.

**A result that reads wrongly** (a missing section, a wrong number, a
calculation marked failed that finished): attach the `.out`, or the part of it
around the problem. ORCAdesk's parser is checked against real ORCA 6.1.1
output, and every new case it gets wrong becomes a test.

Before you attach files, take out anything you do not want public: structures
from unpublished work, names, paths.

## Suggesting a feature

Describe the calculation workflow you are trying to get through (for example
"TS → IRC → Opt/Freq of both ends, for 20 related molecules") and where it is
slow or awkward now. That helps more than a description of a button.

## Working on the code

```bash
git clone https://github.com/wtbdkim/OrcaDesk.git
cd OrcaDesk
pip install -r requirements.txt -r requirements-dev.txt
python main.py                 # run the app
python -m pytest               # the test suite (fast; no ORCA needed)
npx -p typescript tsc --noEmit -p jsconfig.json   # type-check web/ (needs Node)
```

- **Branches**: `main` holds releases only. Branch from `dev` and open pull
  requests against `dev`.
- **Commit messages** on `dev`: `feat:` / `fix:` / `docs:` / `chore:` and a
  one-line summary, then a blank line and the details.
- **Read first**: [CLAUDE.md](CLAUDE.md) is the short operational guide,
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains each subsystem and why
  it is built that way, and [PRINCIPLES.md](PRINCIPLES.md) (code) and
  [DESIGN.md](DESIGN.md) (UI) are the rules the code follows.
- **Tests**: run `python -m pytest` before a pull request. A failing test is
  either a test bug or a product bug; never change a test just to make wrong
  behaviour pass.
- **The UI** (`web/next/`) is plain JavaScript checked by TypeScript through
  JSDoc. After changing it, keep `tsc` at zero errors and re-run
  `tools/uispec.py` / `tools/uidiff.py` (see CLAUDE.md).
- **Docs**: a user-visible change gets a `CHANGELOG.md` entry, and the
  documents that describe the changed part are updated in the same pull
  request.

ORCAdesk drives ORCA; it never bundles or redistributes it. Please keep it that
way in any contribution.
