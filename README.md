# UVSQ CELCAT → live calendar

This repository is preconfigured for:

- **University:** UVSQ
- **CELCAT:** `https://edt.uvsq.fr`
- **Group:** `S5 PHYSIQUE PSC`
- **Calendar timezone:** `Europe/Paris`

It asks CELCAT directly for the group timetable, converts the returned events to
`docs/schedule.ics`, and publishes that file with GitHub Pages.

## Setup — about 5 clicks

### 1. Create a GitHub repository

Create a new repository, for example:

`uvsq-calendar`

It can be **public**. The repo contains no UVSQ password or personal calendar token.
Note that a public repo/page means anyone who knows the URL can see this group's
timetable.

### 2. Upload everything in this folder

Upload the **contents** of this folder to the repository, preserving:

- `.github/workflows/publish-calendar.yml`
- `scripts/update_calendar.py`
- `docs/...`
- `config.json`

The easiest method on GitHub is **Add file → Upload files**.

### 3. Enable GitHub Pages via Actions

In the repository:

**Settings → Pages → Build and deployment → Source → GitHub Actions**

### 4. Run it once

Go to:

**Actions → Publish UVSQ timetable → Run workflow**

After the run succeeds, open:

**Settings → Pages**

GitHub will show a site such as:

`https://YOUR-USERNAME.github.io/uvsq-calendar/`

The actual calendar subscription URL is:

`https://YOUR-USERNAME.github.io/uvsq-calendar/schedule.ics`

The action automatically re-fetches CELCAT four times per hour.

> Calendar apps control how often they refresh subscribed ICS feeds. CELCAT may
> update quickly while Google/Apple/Outlook displays the change later.

## Add to Google Calendar

Use Google Calendar on the web once:

**Other calendars → + → From URL**

Paste the `schedule.ics` URL.

After that it appears in the Google Calendar phone app too. You do **not** need to
open CELCAT for normal use.

## Add to Apple Calendar

On iPhone/iPad:

**Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar**

Paste the `schedule.ics` URL.

On Mac:

**Calendar → File → New Calendar Subscription**

## Change group later

Edit `config.json`:

```json
"group": "S5 PHYSIQUE PSC"
```

Then run the workflow again.

## How it works

CELCAT installations expose calendar event data through a POST request. For group
resources, the request uses resource type `103` and passes the selected group as
`federationIds[]`.

The script tries the common CELCAT endpoint paths automatically, requests the
previous 14 days through the next 180 days, then builds a standards-compatible ICS
feed.

## If the workflow fails

Open the failed Actions run and expand **Fetch CELCAT and generate calendar**.

The script also publishes `status.json`, and the Pages homepage displays the latest
health state.

A failure most likely means UVSQ changed its CELCAT endpoint or blocked server-side
requests. The original CELCAT data remains untouched.

## Local test

```bash
python -m unittest discover -s tests -v
python scripts/update_calendar.py
```

No third-party Python packages are required.
