# Changes for judge review

This copy preserves the source application's core logic and screens. The following packaging changes were made:

- All baked-in production API URL defaults now point to `http://127.0.0.1:5000`.
- Firebase credentials must come from the reviewer's environment or a file outside the repository; no credentials are included.
- `.env` is loaded only from this repository's root, to avoid finding a parent project's environment file.
- Backend authentication is required by default, and the development server binds to localhost with debugging disabled.
- The scientific information base is a sample: the first two existing chunks per topic plus F018 for the cached-follow-up test, with only the matching source references. Broader corpus coverage in the production app is not represented here.
- Native project directories can be regenerated with Capacitor. Signing, local device configuration, generated assets, deployment configuration, and private records are omitted.
- The isolated test runner mocks Firebase initialization and Firestore creation. It does not run queries against a live database.
- Running the inherited tests exposed a retrieval-rule gap: "Is irritability common before a period?" did not match the mood topic or scientific-question rules. This copy adds "irritability" and the "is ... common" question pattern, and strengthens the existing test to assert both routing decisions. This small fix is only in this review copy; the original application folder was not edited.
- A new initial Git commit contains only the curated files. Original commit history and identities are not copied.

No migration to managed Firebase Authentication was performed. The supplied code uses custom account/session logic backed by Firestore.
