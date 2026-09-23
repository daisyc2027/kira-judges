# Kira: Cycle & Mood

Kira brings emotional wellbeing and menstrual-cycle awareness into one iPhone app. Users can log their mood and daily habits, confirm period dates, explore personal patterns, use calming activities, and talk with a supportive AI companion.

- [Published app](https://apps.apple.com/app/id6803115181)
- [Three-minute pitch video](https://www.youtube.com/watch?v=QKg9W2HC31A)
- [Privacy policy](https://sites.google.com/view/kira-wellness-privacy-policy/home)

## About this repository

This is a curated snapshot of Kira's core implementation for hackathon judges. It contains real application code, a representative scientific-information sample, and automated tests. Its fresh Git history begins with this review snapshot; it is not evidence that the entire app was created during a hackathon. Event submissions should disclose the original build and any subsequent work separately.

## What to review

| Feature | Main implementation |
| --- | --- |
| Onboarding and accounts | `HTML/onboarding.html`, `/signup` and `/login` in `app.py` |
| Mood and wellness check-ins | `HTML/home.html`, `HTML/sleep.html`, `HTML/diet.html`, `app.py` |
| Cycle calendar and confirmations | `HTML/cycle.html`, cycle helpers and routes in `app.py` |
| Chat and scientific retrieval | `HTML/chat.html`, `kira_services/`, chat routes in `app.py` |
| Breathing, grounding, urge surfing, haptics, reflection | `HTML/Activities/`, `HTML/interventions.html` |
| Insights and history | `HTML/insights.html`, `HTML/data.html` |
| Native reminders and integration | `HTML/kira-mobile.js`, `capacitor.config.json` |

The AI flow evaluates safety and conversational intent, retrieves relevant facts when useful, adds user/session context, and requests a response from Gemini. Retrieval combines token overlap and deterministic hashed-vector similarity; it does not use a trained medical model or clinical diagnosis engine. RAG provides source context but cannot guarantee correctness.

### Authentication in this snapshot

Firebase Admin and Cloud Firestore provide storage. This code implements its own username/password verification and bearer-session tokens using Firestore account records. It does **not** call the managed Firebase Authentication SDK. This distinction is visible in `hash_password`, `issue_auth_token`, and `require_user_auth` in `app.py`.

The inherited password implementation uses salted SHA-256, and authentication is required by default in this review copy. Treat the copy as a local evaluation build: it has not been hardened or audited for a new public deployment. Use synthetic test accounts and a separate Firebase project.

## Quick review without credentials

Watch the pitch, try the published app, or read the implementation above. To run the isolated tests:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/run_tests.py
```

The test runner replaces Firebase initialization and the Firestore client with mocks before importing the app. It uses no production account, database, or AI credentials. These are unit and route tests, not a complete live-provider or iPhone integration test.

## Run your own evaluation instance

Use Python 3.11 or newer. Install the requirements as above, then:

1. Create a separate Firebase project with a Cloud Firestore database. Obtain a service-account JSON file for that test project and keep it outside this repository.
2. Copy `.env.example` to `.env`. Set `GOOGLE_APPLICATION_CREDENTIALS` to that file's absolute path.
3. For live AI responses, set your own `GEMINI_API_KEY` and a `KIRA_CHAT_MODEL` available to your API project. Without these, AI features may return the app's unavailable-provider message.
4. Start the backend:

```sh
python app.py
```

In another terminal, serve the frontend:

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory HTML
```

Open `http://127.0.0.1:8080` in a fresh browser profile and register a synthetic account. The frontend defaults to `http://127.0.0.1:5000`. If you have previously set a custom server in that browser, remove the `kira_api_url` local-storage entry before testing. The included RAG sample loads locally when your test project's `scientific_facts` collection is empty.

Local evaluation requires your own internet connection and service credentials. Google Fonts is used by the interface. Native haptics and scheduled local notifications require a supported physical device; browser tests cannot verify them.

## Optional native build

Native generated projects and signing details are excluded. With a compatible Node.js installation and Xcode or Android Studio:

```sh
npm ci
npx cap add ios
npx cap sync ios
npx cap open ios
```

Use your own bundle identifier and signing team. For a physical-device evaluation build, replace the localhost API defaults with the HTTPS address of your separate test backend before syncing. The Android equivalents use `android` in place of `ios`.

## Included and excluded

Included: core web interface, backend, RAG/safety/context logic, existing automated tests, dependency manifests, a generic Capacitor configuration, 17 scientific fact chunks and their 15 source references.

Excluded: `.env` values, Firebase service-account credentials, saved cycle/user data, production server address, database exports, full curated corpus and raw research files, signing keys, native build outputs, original Git history, personal local paths, campaign plans, and internal submission documents.

See `REVIEW_CHANGES.md` for differences from the application snapshot. No open-source license is granted by this repository; third-party dependencies and referenced publications retain their own terms.
