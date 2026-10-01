# Sharing the deployed frontend

Parked for a later Scale session. Nothing here has been tested against this
project yet. It's a plan, not a verified recipe.

Lab 2 Step 7 deploys the frontend with `--no-allow-unauthenticated` and reaches
it through `gcloud run services proxy`. That's private, and needs no OAuth
setup. This file covers what an attendee does when they want **other people**
to open it.

Whichever option you pick, the agent itself stays private: only the frontend's
service account (`social-spark-frontend@PROJECT.iam.gserviceaccount.com`) holds
`roles/aiplatform.user` on it. These options only change who can reach the UI.

## Option 1 (recommended): IAP directly on Cloud Run

Identity-Aware Proxy in front of the service: people open the normal
`*.run.app` URL, sign in with Google, and get in only if they've been granted
access. This is GA on Cloud Run directly, with no load balancer needed.

```bash
# enable the IAP API once
gcloud services enable iap.googleapis.com --project YOUR_PROJECT

# turn IAP on for the service
gcloud run services update social-spark-frontend \
  --region us-central1 --project YOUR_PROJECT --iap

# let IAP invoke the service
gcloud run services add-iam-policy-binding social-spark-frontend \
  --region us-central1 --project YOUR_PROJECT \
  --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# let people in: a user, a group, or a whole domain
gcloud iap web add-iam-policy-binding \
  --resource-type=cloud-run --service=social-spark-frontend \
  --region us-central1 --project YOUR_PROJECT \
  --member="user:someone@example.com" \
  --role="roles/iap.httpsResourceAccessor"
```

The exact `gcloud iap web add-iam-policy-binding` flags for a Cloud Run
resource weren't checked here. Confirm them against the docs before this goes
into a lab.

**The workshop catch: projects with no organization.** Most attendees will be
in personal projects. There, IAP needs an OAuth client, and per the docs it
**can't be created from `gcloud` the first time**. So the first enable has to
be done in the console (Cloud Run → the service → Security → Identity-Aware
Proxy), which sets the client up. Inside an organization, Google manages the
OAuth client and the commands above are enough. External (non-org) users also
need the OAuth setup.

Admin roles the person enabling it needs: `roles/run.admin`, `roles/iap.admin`,
plus `roles/iap.settingsAdmin` and `roles/oauthconfig.editor` outside an org.
A project Owner has all of these.

Limitations from the docs: you can't put IAP on both a load balancer and the
Cloud Run service, and IAP is checked before Cloud Run IAM, which breaks
callers that bring their own auth (for example Pub/Sub push). Neither applies
to this frontend.

Docs: https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run

## Option 2: `--allow-unauthenticated` (not recommended here)

```bash
gcloud run services update social-spark-frontend \
  --region us-central1 --project YOUR_PROJECT --no-invoker-iam-check
```

(or redeploy with `--allow-unauthenticated`). It's one flag, but for this app
it means **anyone with the URL can use it**: the app's Google sign-in is
optional by design and falls back to "Continue as guest". So anyone can drive
the agent and spend the attendee's model quota (Gemini 3.1 Pro on every
draft). With `POST_VIA=buffer` they could also publish, since Buffer has no
dry run.

If someone does this anyway: keep `DRY_RUN=true` and `POST_VIA=linkedin`,
keep `--max-instances=3`, set a billing budget alert, and tear it down
afterwards.

## Option 3: make the app's own sign-in mandatory

Remove the "Continue as guest" fallback, register an OAuth client with the
service's redirect URIs, set `NEXT_PUBLIC_GOOGLE_CLIENT_ID`, then make the
service public. That's a code change plus OAuth setup, and it's still weaker
than IAP: the sign-in only gates the UI, so the service itself is still
reachable without signing in. Worth it only if the app needs to know *who*
the user is (e.g. per-user `user_id` for sessions and Memory Bank, which is
currently a fixed `devcamp-user`).

## For the lab

- Add Option 1 as an optional "Share it" section after Lab 2 Step 7, with the
  console route for no-org projects as the main path.
- Test it end to end on a no-org project first: enable via console, grant a
  second Google account, open the URL in a private window.
- Mention Option 2 only as the thing *not* to do, and why.
