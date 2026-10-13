# Push to GitHub, step by step

Run everything from the project root (the folder containing `README.md`).

## 0. Pre-flight (5 minutes, do not skip)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                                   # must say: 10 passed
uvicorn app.main:app                                  # open http://localhost:8000
```

Run one real backtest and one live session (see the README checklist). If something fails, fix it before pushing: a broken first commit is the first thing a reviewer sees.

## 1. Check what you are about to commit

```bash
git init
git add .
git status
```

You should NOT see `.env`, `.venv/`, `__pycache__/`, or anything under `data/` except `.gitkeep`. If you do, `.gitignore` is not being picked up: stop and fix it.

## 2. First commit

```bash
git branch -M main
git commit -m "Strategy Desk: backtester, live paper engine, DB, API and web UI"
```

If git asks who you are:

```bash
git config --global user.name "Viraj Devkar"
git config --global user.email "you@example.com"
```

## 3. Create the GitHub repo and push

**Option A: GitHub CLI**

```bash
gh auth login
gh repo create strategy-desk --public --source=. --remote=origin --push
```

**Option B: website**

1. github.com -> New repository -> name `strategy-desk`. Do **not** add a README, .gitignore or licence (you already have them).
2. Run the commands GitHub shows:

```bash
git remote add origin https://github.com/YOUR_USER/strategy-desk.git
git push -u origin main
```

## 4. Make the repo look finished

1. In `README.md`, replace `YOUR_USER` in the badge URL. After the first push, **Actions** runs the tests and the badge turns green when they pass.
2. Add screenshots: run a backtest and a live session, save two screenshots in `docs/`, and reference them in the README:
   ```markdown
   ![Backtest](docs/backtest.png)
   ![Live](docs/live.png)
   ```
3. Repo **About** box: description "Backtest and live paper-trading engine with FastAPI, SQLAlchemy and a web UI"; topics `quant`, `backtesting`, `algorithmic-trading`, `fastapi`, `python`.
4. Optional: add a `LICENSE` (MIT) through GitHub's "Add file" -> "Create new file" -> type `LICENSE` -> choose a template.

```bash
git add docs README.md && git commit -m "Add screenshots and badge" && git push
```

## 5. What GitHub will and will not do

- GitHub **stores and shows** your code and runs your tests (Actions).
- GitHub **cannot host the running backend**. GitHub Pages only serves static files. For a public live demo, deploy the Docker image to Render, Railway or Fly.io and point `DATABASE_URL` at their Postgres. That is optional; a clean repo plus a short screen recording is enough for interviews.

## 6. Day to day

```bash
git add -A
git commit -m "Describe the change"
git push
```

## Safety

- Never commit `.env`, API keys, or database files.
- If you commit a secret by mistake, rotate the secret first. Deleting the commit does not un-leak it.
