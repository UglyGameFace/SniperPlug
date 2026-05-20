# Put SniperPlug on GitHub

## 1. Create the repo

Create an empty GitHub repo, for example:

```text
UglyGameFace/SniperPlug
```

Do not commit your `.env` file.

## 2. Push the project

From the project folder:

```bash
git init
git add .
git commit -m "Initial SniperPlug Discord bot"
git branch -M main
git remote add origin https://github.com/UglyGameFace/SniperPlug.git
git push -u origin main
```

Replace the repo URL if you use a different name.

## 3. Secrets

Never put this in GitHub:

```env
DISCORD_TOKEN=
```

Set that only in Discloud environment variables.

## 4. GitHub Actions check

This repo includes:

```text
.github/workflows/python-check.yml
```

It installs requirements and runs:

```bash
python -m compileall .
```

That catches basic syntax/import problems before you deploy.
