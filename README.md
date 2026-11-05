# Apple refurbished stock bot: user guide

A Telegram bot that watches the **Apple Singapore refurbished store**
(apple.com/sg/shop/refurbished) and tells you when something you want comes into stock,
changes price or sells out. Everything is done from the Telegram chat.

> Setting it up or changing how it runs? See [SETUP.md](SETUP.md).

## Contents
1. [Quick start](#quick-start)
2. [Commands](#commands)
3. [What to watch](#what-to-watch)
4. [Alerts you get](#alerts-you-get)
5. [Settings](#settings)
6. [Good to know](#good-to-know)

---

## Quick start

```
/stock mac                      every refurbished Mac, cheapest first, numbered
/stock mac air m4               only MacBook Airs with the M4 chip
/watch mac air m4 16gb max=2000 alert me about matching MacBook Airs
/watch 3                        watch item 3 from the /stock list (that exact part number)
/list                           my watches and what's in stock for each
```

The bot checks the store **every 5 minutes** and reads your commands at the same time, so a
reply can take **up to about 5 minutes**.

---

## Commands

| Command | What it does |
|---|---|
| `/stock` | Each section with its number of items. |
| `/stock mac` | Every item in a section, cheapest first, numbered, 30 per page. |
| `/stock mac air m4 16gb` | Only items matching those words (the same words as `/watch`). |
| `/stock mac air page 2` | The next page. |
| `/watch …` | Start a watch. The bot works out which kind from what you type (see below). |
| `/list` | Your watches, numbered, with how many matching items are in stock now and the cheapest. |
| `/unwatch 2` | Stop watch 2. `/unwatch 2 5` stops several; `/unwatch all` stops all. The rest are renumbered. |
| `/settings` | Show the settings. `/settings price drops` etc. changes them (see [Settings](#settings)). |
| `/status` | What this check downloaded, and whether Apple's store is loading. |
| `/help` | The command list. |

The sections are `mac`, `ipad`, `watch`, `airpods`, `appletv`, `homepod` and `accessories`.
They're the tabs of Apple's refurbished store. Some items are in two sections: Studio Display is
under both `mac` and `accessories`, for example.

---

## What to watch

One `/watch` command covers four kinds of watch:

| You type | Kind | Alerts for |
|---|---|---|
| `/watch mac` | 📦 **Section** | Every item in that section. |
| `/watch mac air 13 m4 16gb` | 🔎 **Specs** | Items in that section that match **all** the words. |
| `/watch FX2E3ZP/A` or `/watch FX2E3` | 🏷 **Part number** | That exact configuration. |
| `/watch 3` (or `/watch 3 5 8`) | 🏷 **Part number** | Item 3 of your most recent `/stock` list. |

### Spec words

Start with the section. The words after it can come in any order:

| Word | Means | Examples |
|---|---|---|
| model words | `air`, `pro`, `mini`, `neo`, `imac`, `studio`, `macbook`, `display`, `se`, `series`, `ultra`, `pencil` … are matched against the **product name only**. `pro` means MacBook Pro / iPad Pro, not "M4 Pro chip". | `mac air`, `mac mini`, `ipad pro`, `watch se` |
| screen size | A bare number in `mac` / `ipad`, or with `-inch`. | `13`, `14`, `15`, `24`, `12.9`, `11-inch` |
| case size | Watch case size. | `42mm`, `46mm` |
| chip | Exactly that chip: `m4` does **not** match M4 Pro or M4 Max. | `m4`, `m4 pro`, `m5 max`, `a18 pro`, `chip=m4pro` |
| cores | Text from the title. | `10-core`, `14-core cpu`, `32-core gpu` |
| memory | `16gb` matches **RAM or SSD**. To be exact use `ram=` / `ssd=` or put `ram` / `ssd` after it. | `16gb`, `1tb`, `ram=24gb`, `ssd=1tb`, `512gb ssd` |
| year | Apple's release-year filter. | `2025`, `year=2024` |
| nano-texture | `nano=yes`, `nano=no`, `nano=any` (the default). `nano` alone = yes. | `nano=no` |
| price | Highest price in S$. | `max=1800`, `max 1.8k`, `under 2000` |
| colour, anything else | Must appear in Apple's title or filters. `grey`/`gray` and `aluminium`/`aluminum` both work. | `midnight`, `space black`, `silver`, `cellular`, `titanium`, `ethernet` |

When you add a watch, the bot replies with what matches **right now**. If a word doesn't appear in
any item (a typo, for example), it says so. Use `/unwatch N` and try again.

Shortcuts: `macbook` and `imac` imply the `mac` section. `/watch macbook air m4` is the same as
`/watch mac macbook air m4`.

### Examples

```
/watch mac air 13 m4 16gb 512gb midnight max=1800
/watch mac pro 14 m4 pro ram=24gb ssd=1tb nano=no
/watch mac mini m4 pro
/watch imac 24 nano=yes
/watch mac studio display
/watch ipad pro 11 m4 cellular 512gb
/watch ipad mini a17 pro
/watch watch series 11 46mm titanium
/watch homepod
```

### Part numbers

Apple's refurbished part numbers look like `FX2E3ZP/A` or `G1N23ZP/A` (watches: `FEH34X/A`).
They start with **F** or **G**. The bot matches the **first 5 characters**, so `FX2E3` works.
You can find them on the product page or in `/stock` lists.

- **In stock now:** you get its details straight away.
- **Sold out but seen before:** the bot says when it sold out and its last price.
- **Never seen:** accepted, with a ⚠️ "can't confirm it exists" note. The bot doesn't know its
  section, so it checks **all 7 sections** every run until the part shows up. Name the section
  so it only checks one: `/watch FX2E3 mac`.

---

## Alerts you get

| Alert | When | Sound |
|---|---|---|
| 🟢 **In stock** | An item matching a watch appears: either a **new listing** or **back after** some time (with its old price if it changed). 🆕 marks a model seen for the first time. | 🔔 |
| 📉 **Price drop** / 📈 **Price up** | A watched item's price changed. Old → new and the difference. An item that only now drops under your `max=` counts too. | 🔔 drop / 🔕 up |
| ⚪ **Sold out** | A watched item disappeared from the store. Shows how long it was in stock, if known. | 🔕 |
| 🆕 **New model** | A model (product + chip, e.g. "16-inch MacBook Pro, M5 Max") appears for the first time in a section you have a section or spec watch on. | 🔔 |
| ⚠️ **Can't read Apple's store** | Apple's page has failed for **30 minutes**. Then ✅ **loading again** when it works. | 🔔 |
| ☀️ **Daily summary** | Every day at 09:00 Singapore time: how many watched items came into stock, changed price or sold out since the last summary, and every watch with what's in stock now. | 🔕 |
| ⚠️ **GitHub run failed** | Once per failure streak, not every 5 minutes. | 🔔 |

**One alert per item.** If several watches match the same item, it comes once, labelled with every
matching watch ("watches #1, #3"). Several items changing in the same check come in one message.

During **quiet hours** (off by default), every alert arrives silently.

---

## Settings

`/settings` shows them all. Change one or several at once:
`/settings price drops quiet 23-7 mingone 30`.

| Setting | Default | Values | What it does |
|---|---|---|---|
| `restock` | on | on / off | 🟢 In-stock alerts. |
| `price` | on | on / drops / off | Price alerts: any change, only drops, or none. |
| `soldout` | on | on / off | ⚪ Sold-out alerts. |
| `newmodel` | on | on / off | 🆕 New-model alerts. |
| `health` | on | on / off | ⚠️ / ✅ alerts about Apple's store not loading. |
| `daily` | 9 | hour 0-23 / off | Hour of the daily summary (Singapore time). |
| `quiet` | off | e.g. `23-7` / off | Alerts in these hours (Singapore time) arrive silently. |
| `mingone` | off | minutes / off | Ignore short disappearances. With `mingone 30`, an item gone for less than 30 minutes is never reported as sold out, and its return isn't reported as a restock. |

---

## Good to know

- **A failed or empty download never counts as "sold out".** If Apple's page doesn't load, that
  section is skipped. If a section suddenly comes back **empty or less than half its size**, the
  bot waits for the next check (5 minutes later) to confirm it before reporting anything.
- **How "in stock" works:** Apple removes sold-out items from the refurbished store and adds them
  back when restocked. "In stock" means the item is listed.
- Only the sections you watch are downloaded. `/stock` downloads a section once when you ask.
- Messages from anyone other than you are ignored, without a reply.
- **The repo is public:** your watches and the last-seen stock are in `state.json` for anyone to
  read. The bot token and your chat ID are GitHub secrets and appear nowhere.
- The bot can only alert you while GitHub runs it. If cron-job.org stops (for example when its
  GitHub token expires), cron-job.org emails you.
