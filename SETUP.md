# Apple refurbished stock bot: setup & technical details

Checks the Apple Singapore refurbished store every 5 minutes on GitHub Actions and sends alerts
to Telegram. Python 3.8+, standard library only (nothing to install).

> **Just want to use it?** The [user guide](README.md) explains everything you do from Telegram.

## Contents
1. [Files](#files)
2. [Setting it up](#setting-it-up)
3. [The 5-minute timer (cron-job.org)](#the-5-minute-timer-cron-joborg)
4. [How it works](#how-it-works)
5. [Public repo: what is visible](#public-repo-what-is-visible)
6. [When something goes wrong](#when-something-goes-wrong)
7. [Updating the bot](#updating-the-bot)
8. [Testing on your PC](#testing-on-your-pc)

---

## Files

| File | What it is |
|---|---|
| `README.md` | The user guide (shown on the repo's front page). |
| `SETUP.md` | This page. |
| `bot.py` | The bot. |
| `.github/workflows/check.yml` | Runs the bot, has the **Run workflow** button, saves `state.json` back to the repo, and messages you if a run fails. |
| `state.json` | Your watches, settings and the last-seen stock. Created by the first run and committed by the workflow **only when something changed**. **Don't edit or upload it.** |
| `.github/workflows/apple-test.yml` | The one-off download test. Delete it once the bot works (optional). |

---

## Setting it up

1. **Create a new Telegram bot.** In Telegram, message **@BotFather**, send `/newbot`, follow the
   prompts and copy the **bot token** (looks like `123456789:AA...`).
   **Don't reuse the flight tracker's bot**: two programs reading one bot's messages take each
   other's commands.
2. **Your chat ID.** It's the **same number as in the flight tracker** (in a private chat, the chat
   ID is your Telegram user ID). If you don't have it noted: open the new bot, press **Start**, send
   it any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in your browser (your
   token in place of `<TOKEN>`) and copy the number after `"chat":{"id":`.
   Do this only in your own browser, never in a GitHub issue or workflow.
3. **Keep your email out of commits.** github.com → profile picture → **Settings** → **Emails** →
   tick **Keep my email addresses private** (and **Block command line pushes that expose my
   email**). Commits you make on the website then use a `@users.noreply.github.com` address.
4. **Create the repo.** github.com → **New repository** → a name such as `refurb-bot` →
   **Public** → tick **Add a README file** → **Create repository**. (You can reuse the test repo
   from step 1 of the build instead.)
5. **Upload the files.**
   - **Add file → Upload files** → drag in `bot.py`, `README.md` and `SETUP.md` (replace the
     README that GitHub made) → **Commit changes**.
   - **Add file → Create new file** → type `.github/workflows/check.yml` as the name → paste the
     contents of `check.yml` → **Commit changes**. (Folders starting with a dot don't upload well
     by dragging, so the workflow is pasted.)
6. **Add the secrets.** Repo **Settings → Secrets and variables → Actions → New repository
   secret**, twice:
   - `TELEGRAM_BOT_TOKEN`: the bot token
   - `TELEGRAM_CHAT_ID`: your chat ID
7. **Check that runs may commit.** Repo **Settings → Actions → General → Workflow permissions**:
   **Read and write permissions** should be selected. (`check.yml` asks for write access itself;
   this setting only matters if your account defaults to read-only.)
8. **First run.** **Actions** tab → **Check Apple refurbished stock** → **Run workflow**. Within a
   minute you get "✅ Apple refurbished bot is running" and a silent status. A `state.json` commit
   appears in the repo.
9. **Try it.** Send `/stock mac` to the bot, then press **Run workflow** again (or wait for the
   timer). You should get the list.
10. **Start the timer.** See [the next section](#the-5-minute-timer-cron-joborg).

The bot only reads commands from, and only sends to, the chat in `TELEGRAM_CHAT_ID`. Messages from
anyone else are skipped without a reply and without being logged.

---

## The 5-minute timer (cron-job.org)

Same setup as the flight tracker, with a 5-minute schedule. GitHub's own `schedule:` trigger isn't
used: it's best effort and is often late or skipped.

**1. Find the branch name.** The branch button above the repo's file list shows it (usually `main`).

**2. Create a GitHub token that can only start this repo's workflows.**
1. github.com → profile picture → **Settings** → **Developer settings** → **Personal access
   tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Token name**: `refurb-bot-timer`. **Expiration**: the longest offered (note the date).
3. **Repository access**: **Only select repositories** → your refurb repo.
4. **Permissions** → **Repository permissions** → **Actions**: **Read and write**. Nothing else.
5. **Generate token** and copy it (starts with `github_pat_`). It's shown only once.

**3. Create the cron job.**
1. Log in to cron-job.org → **Dashboard** → **Create cronjob**.
2. **Common** tab:
   - **Title**: `Refurb bot`
   - **URL**: `https://api.github.com/repos/<your-user>/<repo>/actions/workflows/check.yml/dispatches`
   - **Execution schedule**: **Every 5 minutes**.
3. **Advanced** tab:
   - **Request method**: **POST**
   - **Headers** (add three):

     | Key | Value |
     |---|---|
     | `Authorization` | `Bearer github_pat_…` (your token) |
     | `Accept` | `application/vnd.github+json` |
     | `X-GitHub-Api-Version` | `2022-11-28` |

   - **Request body**: `{"ref":"main","inputs":{"source":"timer"}}`, with your branch name if
     it isn't `main`.
4. **Save**, then **Test run**. The response should be **204 No Content**, and a **Timer check**
   run appears in the Actions tab. Timer runs send nothing unless there's an alert or a reply.

| Test run response | Meaning |
|---|---|
| `204` | Working. |
| `401` | Token wrong or expired: check the `Authorization` header (`Bearer`, a space, the token). |
| `403` / `404` | The token can't see the repo or lacks **Actions: Read and write**, or the URL has a typo. |
| `422` | Wrong branch name in the body, or `check.yml` isn't uploaded yet. |

cron-job.org emails you if the requests start failing (for example when the token expires).

**GitHub minutes.** Actions are free and unlimited for public repos on GitHub's standard runners,
so 288 runs a day cost nothing. A run takes about 10-20 s. Runs never overlap. If one is still going
when the next starts, the new one waits.

---

## How it works

**Data.** For each section you watch, the bot downloads
`https://www.apple.com/sg/shop/refurbished/<section>` with a plain HTTP request and reads the
`window.REFURB_GRID_BOOTSTRAP` JSON in the page. Each item has a part number, title, price (S$) and
Apple's filter values (model, screen size, year, colour, RAM `tsMemorySize`, storage
`dimensionCapacity`; iPad connectivity; Watch case size, material and connection). Sold-out items
simply disappear from the list. A section with nothing in it has `"tiles": null`.

**Each run, in order:**
1. Read new Telegram messages (`getUpdates`) and answer commands. Commands that need stock data
   download it. Every section is downloaded at most once per run.
2. Download every watched section and compare with the last run's snapshot in `state.json`:
   new part numbers → 🟢, missing ones → ⚪, different prices → 📉/📈, and a product + chip never
   seen in that section → 🆕. All alerts of one kind go in one message, one line per item.
3. Bot health: if any watched section failed (after one retry), note when failures started, and
   alert after 30 minutes. The first fully successful run sends ✅ and clears it.
4. Daily summary, if it's past the summary hour (SGT) and it hasn't been sent today.
5. Save `state.json` if anything in it changed. The workflow commits it only then, so a quiet
   day has few commits.

**Safety rules**
- A failed download (HTTP error, timeout, no stock JSON, odd format) skips that section. Nothing
  is counted as sold out and the snapshot is kept.
- If a section comes back empty, or with less than half its items (when it had 10+), the bot waits
  for the next run. Only if that run agrees is it treated as real.
- The first download of a section only remembers what's there. No alerts.
- Sold-out items are remembered for 60 days, so a restock can say "back after 3 d" and show the
  old price.
- `mingone`: a sold-out alert waits until the item has been gone that long. If it comes back
  sooner, neither alert is sent.

**state.json** holds: `watches`, `settings`, `snap` (last-seen items per watched section),
`families` (models seen per section), `gone` (sold-out items), `health`, `daily` (counts for the
summary), `last_list` (numbers for `/watch N`), `telegram_offset`. Sections nobody watches are
removed from it. It holds no chat ID or token.

---

## Public repo: what is visible

| Where | What anyone can see | What they can't |
|---|---|---|
| Code | `bot.py`, the workflow, the docs | No personal details are in them. |
| Commits | "Update state" commits by github-actions[bot], and yours (with a noreply email if step 3 is done) | |
| `state.json` | Your watches, settings and the last-seen stock | No chat ID, token or message text. |
| Actions logs | Counts only: "mac: 193 items (1 new, 0 gone, 0 price changes)", "2 command(s) answered" | Never message text, commands, names or chat IDs. Secrets are masked anyway. |

If you'd rather keep your watches private, a private repo works the same way, but it uses
Actions minutes: 288 runs a day is well over the 2,000 free minutes a month.

---

## When something goes wrong

| Symptom | Cause / fix |
|---|---|
| No reply to commands | Commands are read every 5 minutes. Check that the timer runs (Actions tab). Press **Run workflow** to force a run. |
| Run fails: "secrets must both be set" | Step 6: both secrets, exact names. |
| Run fails: `Unauthorized` in the log | The token is wrong or was revoked in BotFather. Re-paste it. |
| Run fails: `chat not found` | Wrong chat ID, or you haven't pressed **Start** in the bot's chat. |
| Commands ignored, log says "messages from other chats ignored" | `TELEGRAM_CHAT_ID` isn't your chat. Get the number again (step 2). |
| `Conflict: terminated by other getUpdates request` | Another program uses the same bot (e.g. the flight tracker's bot). Make a separate bot. |
| ⚠️ Can't read Apple's store | Apple's page is down or changed. If it lasts days, Apple may have changed the page: run the `apple-test` workflow and send me its output. |
| Save step fails: "state.json changed on GitHub" | Someone edited `state.json` on GitHub. Don't. The next run fixes itself. |

The ⚠️ "GitHub run failed" message is sent once when runs start failing, not every 5 minutes.
cron-job.org emails you if it can't start runs.

---

## Updating the bot

Upload the new `bot.py` (or docs) with **Add file → Upload files**. Same names replace the old
ones. For `check.yml`, open it on GitHub → pencil icon → paste → **Commit changes**.
**Never upload `state.json`:** the copy on GitHub is the live one. Your watches and settings
survive updates.

To pause the bot, turn off the job in cron-job.org. To stop it completely, also disable the
workflow (Actions → Check Apple refurbished stock → ⋯ → Disable workflow).

---

## Testing on your PC

The bot has a dry-run mode that prints messages instead of sending them:

```powershell
$env:DRY_RUN = "1"
$env:STATE_FILE = "$env:TEMP\refurb-state.json"
$env:FAKE_COMMANDS = "/stock mac air m4`n/watch mac air m4 16gb"
python bot.py
```

Other test variables: `FAKE_DATA_DIR` (a folder with `<section>.json` files containing the
bootstrap JSON, used instead of downloading) and `NOW` (an ISO time to pretend it is, for the daily
summary and the 30-minute health alert).
