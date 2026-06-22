# Garmin Tracker — Azure Deployment Plan

**Status:** Ready for Validation
**Mode:** NEW

## 1. Workspace analysis

Greenfield. Previous Python-script prototype was removed because the user wants
a static PWA that reads watch data on their phone. Pure browser cannot scrape
Garmin Connect (CORS + credentials), so we add a small serverless backend.

## 2. Requirements

- Show the user's latest Garmin watch data on a phone (steps, resting HR, sleep,
  body battery, recent activities, last 30-day trends).
- Installable PWA, FishlerHousehold visual format.
- Data syncs automatically; user does not enter anything manually.
- Single-user (personal). No multi-tenant auth.
- Budget: hobby-tier. Should fit Azure free / consumption pricing.

## 3. Components & technologies

| Component | Tech | Hosting |
|---|---|---|
| `web/` PWA | Single-file HTML + vanilla JS (Firebase Web SDK from CDN) | Azure Static Web Apps (Free) |
| `api/` sync function | Python 3.12, `garminconnect`, `firebase-admin`, `azure-keyvault-secrets` | Azure Functions, Flex Consumption |
| Secrets | Garmin email/password, Firebase service-account JSON | Azure Key Vault, accessed via Function's system-assigned managed identity |
| Telemetry | Application Insights | Linked workspace + component |
| Data store | Firestore (single document per profile) | **External — Firebase project, user-managed.** Function uses Admin SDK, PWA uses Web SDK. |

## 4. Recipe

**AZD + Bicep.** Single resource group, single environment to start.

## 5. Architecture

```
Garmin Watch
   |  (BLE)
Garmin Connect mobile app on phone
   |
Garmin Connect cloud  <-------+
                              |  (hourly timer + manual HTTP)
Azure Function (Python)  ---->+--> Firestore document
   ^                              garminTrackers/{profileId}
   | reads secrets from               |
   | Azure Key Vault                  | onSnapshot
   |                                  v
   +-- Managed Identity         PWA on phone
                                (Azure Static Web Apps)
```

- Function is the **only** thing that writes to Firestore.
- PWA only reads (Firebase Web SDK `onSnapshot`).
- A manual "Sync now" button in the PWA calls the Function's HTTP trigger
  (auth via function key stored in PWA `localStorage`).
- CORS on the Function allows the Static Web App's hostname.

## 6. Security & secrets

- All secrets in Azure Key Vault (RBAC mode):
  - `garmin-email`
  - `garmin-password`
  - `firebase-service-account-json` (whole JSON blob)
- Function App: system-assigned managed identity, granted
  **Key Vault Secrets User** on the Key Vault.
- Storage account: no public blob containers; deployment via AzureWebJobsStorage.
- Function key gates the HTTP trigger; PWA passes it via `x-functions-key`.

## 7. External prerequisites (user must do once)

1. Create a Firebase project.
2. Enable Cloud Firestore (production mode, single region).
3. Add a "Web app" → copy `firebaseConfig` → paste into `web/index.html`.
4. Service accounts → "Generate new private key" → JSON file.
   Will be uploaded into Key Vault as `firebase-service-account-json`
   after first `azd up`.
5. Firestore security rules: allow reads of `garminTrackers/{profileId}` for
   "anyone with the project's web API key" (acceptable for personal app) or
   add an App Check / anonymous-auth gate later.

## 8. Outputs

- `web/` — PWA (Static Web App content root)
- `api/` — Function App code
- `infra/` — Bicep templates
- `azure.yaml` — azd manifest

## 9. Open items

- Firebase project creation is manual (no Azure ARM provider for it).
- Garmin auth tokens are not persisted across cold starts in v1
  (acceptable with hourly timer). Future: cache to blob storage.

## 10. Status checklist

- [x] Plan written
- [ ] User approved plan
- [ ] Artifacts generated
- [ ] Security hardened
- [ ] Status set to `Ready for Validation`
- [ ] azure-validate invoked
- [ ] azure-deploy invoked
