# Garmin Tracker

A personal PWA that shows your Garmin watch data on your phone. Pulled hourly
from Garmin Connect by a GitHub Actions workflow, stored in a Firestore
document, and rendered live by a single-file HTML app installable from your
browser.

```
Watch ─BLE→ Garmin Connect app ─→ Garmin Connect cloud
                                       ↑
                                       │  hourly + on-demand
                                       │
                      GitHub Actions (Python) ─→ Firestore doc
                                                       │
                                                       │ live updates
                                                       ▼
                                          PWA on phone (GitHub Pages)
```

This deployment uses **zero paid services**:

- GitHub Actions runs the sync (free on public repos, 2,000 min/month on private)
- GitHub Pages hosts the PWA (free)
- Firebase Spark plan stores the Firestore doc (free up to 1 GB / 50k reads/day)

## What's in this repo

| Path | Purpose |
|---|---|
| `web/` | The PWA. Single-file HTML + manifest + icons. Deployed to GitHub Pages. |
| `scripts/sync.py` | Python sync script. Reads Garmin Connect, writes Firestore. |
| `.github/workflows/sync.yml` | Hourly cron + manual `workflow_dispatch` to run the sync. |
| `.github/workflows/pages.yml` | Deploys `web/` to GitHub Pages on push to `main`. |
| `api/`, `infra/`, `azure.yaml` | Old Azure Functions / Bicep version. Unused; kept for reference. |

## One-time setup

### 1. Firebase project (data store)

1. Go to <https://console.firebase.google.com/> → **Add project** (any name).
2. **Build → Firestore Database → Create database** in production mode, single region.
3. **Project settings → General → Your apps → Add app → Web** (`</>`).
   Skip Firebase Hosting. Copy the `firebaseConfig` object.
4. Paste those values into [web/index.html](web/index.html), replacing the
   `REPLACE_…` placeholders in the `FIREBASE_CONFIG` block.
5. **Project settings → Service accounts → Generate new private key** → save
   the downloaded JSON file. You'll paste its contents into a GitHub Actions
   secret in step 4.

### 2. Firestore security rules

For a personal app: anyone with the web API key can read the single profile
doc, only the Admin SDK (the GitHub Actions job) can write. Firestore console
→ **Rules**:

```js
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /garminTrackers/{profileId} {
      allow read: if true;
      allow write: if false;
    }
  }
}
```

The Admin SDK bypasses these rules, so the workflow can still write.

### 3. Push this repo to GitHub

From the repo root:

```powershell
git init -b main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/JuliasStuff/Garmin.git
git push -u origin main
```

If `git push` rejects because the remote already has commits (e.g. an
auto-created README), pull-rebase first:

```powershell
git pull --rebase origin main
git push -u origin main
```

### 4. Add the three GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Create all three:

| Name | Value |
|---|---|
| `GARMIN_EMAIL` | your Garmin Connect login email |
| `GARMIN_PASSWORD` | your Garmin Connect password |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | paste the **entire contents** of the service-account JSON file from step 1.5 (open in a text editor, copy-all, paste) |

### 5. Enable GitHub Pages

Repo → **Settings → Pages**. Under **Build and deployment**, set **Source** to
**GitHub Actions**. The `pages.yml` workflow does the rest on the next push.

After it deploys, your PWA URL is:

```
https://juliasstuff.github.io/Garmin/
```

### 6. Run the first sync

Repo → **Actions** tab → **Garmin sync** workflow → **Run workflow** → **Run
workflow**. Wait ~30–60 seconds; on success the Firestore doc
`garminTrackers/default` is written, and the PWA will live-update.

After this, the cron schedule (`0 * * * *`) takes over and runs hourly.
GitHub-hosted cron is best-effort and can drift up to ~15 min, which is fine
for this use case.

### 7. Wire the manual "Sync now" button (optional)

If you want the PWA button to trigger an out-of-band sync:

1. <https://github.com/settings/personal-access-tokens/new> → create a
   **fine-grained** PAT:
   - **Resource owner**: your user
   - **Repository access**: Only select repositories → `JuliasStuff/Garmin`
   - **Repository permissions** → **Actions**: **Read and write**
   - Expiration: whatever you're comfortable with
2. Copy the token (`github_pat_…`).
3. Open the deployed PWA → **Setup** tab → paste into **GitHub PAT** → **Save**.
4. Hit **Test sync**. It calls
   `POST /repos/JuliasStuff/Garmin/actions/workflows/sync.yml/dispatches`.

The PAT is stored in `localStorage` on your phone. The repo is public; the
sync script and secrets are not exposed (Actions Secrets only decrypt inside
the workflow runner).

### 8. Install on your phone

Open the GitHub Pages URL in your phone's browser, then:

- **iOS Safari**: Share → Add to Home Screen
- **Android Chrome**: ⋮ menu → Install app

## Local development

```powershell
# Run the sync once locally to verify config
cd scripts
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:GARMIN_EMAIL = "you@example.com"
$env:GARMIN_PASSWORD = "your-password"
$env:FIREBASE_SERVICE_ACCOUNT_JSON = Get-Content C:\path\to\sa.json -Raw
python sync.py

# PWA — any static file server works
cd ..\web
python -m http.server 5500
# Open http://localhost:5500
```

## Known limits / notes

- **Custom intensity minutes**: the app does not use Garmin's built-in
  intensity-minute scoring. It counts each sampled minute where heart rate is
  at least 110 bpm as 1 intensity minute. Override the threshold with
  `GARMIN_INTENSITY_HR_THRESHOLD` if needed.

- **Garmin MFA**: this sync runs unattended. If your Garmin account uses MFA,
  the workflow will fail with a clear message. Use a Garmin account without
  MFA, or extend `scripts/sync.py` to use a pre-generated Garth token cached
  somewhere persistent.
- **Cold logins**: every workflow run re-logs into Garmin. If Garmin throttles
  the account, lower the cron frequency (e.g. `0 */3 * * *` for every 3 hours).
- **Cron drift**: GitHub Actions cron can be delayed up to ~15 min during peak
  load. Acceptable for hourly health data.
- **Single profile**: the Firestore doc path is `garminTrackers/default`. To
  track multiple people, change `GARMIN_PROFILE_ID` (env var in `sync.yml`)
  per deployment.
- **PAT security**: the PAT lives in browser `localStorage`. Anyone with
  physical access to your unlocked phone can use it to trigger workflow runs
  on the repo. They can't read your secrets. Revoke it at
  <https://github.com/settings/personal-access-tokens> if compromised.

## The old Azure version

The `api/`, `infra/`, and `azure.yaml` files are an earlier Azure Functions +
Static Web Apps implementation. They're unused by the current GitHub Actions
flow but kept in the repo as a reference / migration path.
