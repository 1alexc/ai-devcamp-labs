#!/usr/bin/env bash
# One-time manual GCP setup for this project — a growing checklist, not a
# single-purpose script. Every step below is written to be idempotent, so
# it's safe to re-run the whole file each time a new step is added rather
# than tracking which ones you've already done.
#
# Uses whichever Google account gcloud is logged in as (override with
# PERSONAL_EMAIL=you@gmail.com ./gcp-setup.sh). Needs GOOGLE_CLOUD_PROJECT in
# backend/social_poster/.env (copy .env.example there first), then run:
#   ./gcp-setup.sh
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="backend/social_poster/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
if [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]]; then
  echo "GOOGLE_CLOUD_PROJECT is not set. Copy .env.example to $ENV_FILE and set it, then re-run."
  exit 1
fi

# === Step 1: Check login & ADC (with quota project) =========================
echo "=== Step 1: gcloud auth & ADC quota project ==="
current_account="$(gcloud config get-value account 2>/dev/null || true)"
PERSONAL_EMAIL="${PERSONAL_EMAIL:-$current_account}"
if [[ -z "$PERSONAL_EMAIL" ]]; then
  echo "No gcloud account found. Run 'gcloud auth login', then re-run this script."
  exit 1
fi
echo "Using $PERSONAL_EMAIL."

ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
if [[ -f "$ADC_FILE" ]]; then
  echo "Application Default Credentials already set up."
else
  echo "Setting up Application Default Credentials..."
  gcloud auth application-default login
fi

echo "Setting ADC quota project to $GOOGLE_CLOUD_PROJECT..."
gcloud auth application-default set-quota-project "$GOOGLE_CLOUD_PROJECT"
echo

# Retrieve project number for Agent Identity principalSet
PROJECT_NUMBER="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')"
AGENT_IDENTITY_MEMBER="principalSet://agents.global.proj-${PROJECT_NUMBER}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"

# === Step 2: APIs needed for Agent Runtime deploys ==========================
echo "=== Step 2: APIs for Agent Runtime ==="
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT" >/dev/null
echo "Enabled (or already enabled)."
echo

# === Step 3: GCS image bucket in us-central1 (private by default) ===========
echo "=== Step 3: GCS image bucket in us-central1 ==="
GCS_BUCKET_NAME="${GCS_BUCKET_NAME:-${GOOGLE_CLOUD_PROJECT}-socialspark}"
GCS_LOCATION="us-central1"
echo "Project:     $GOOGLE_CLOUD_PROJECT"
echo "Bucket:      $GCS_BUCKET_NAME ($GCS_LOCATION)"

if gcloud storage buckets describe "gs://$GCS_BUCKET_NAME" --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
  echo "Bucket gs://$GCS_BUCKET_NAME already exists, skipping create."
else
  echo "Creating private bucket gs://$GCS_BUCKET_NAME in $GCS_LOCATION..."
  gcloud storage buckets create "gs://$GCS_BUCKET_NAME" \
    --project="$GOOGLE_CLOUD_PROJECT" \
    --location="$GCS_LOCATION" \
    --uniform-bucket-level-access
  echo "Created bucket gs://$GCS_BUCKET_NAME."
fi

# Set up local signing service account for local development
SA_NAME="social-spark-uploader"
SA_EMAIL="${SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
echo "Uploader SA: $SA_EMAIL"

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
  echo "Service account already exists, skipping create."
else
  echo "Creating service account..."
  gcloud iam service-accounts create "$SA_NAME" \
    --project="$GOOGLE_CLOUD_PROJECT" \
    --display-name="Social Spark image uploader (signs GCS URLs, never downloads a key)"
fi

echo "Granting $SA_EMAIL write access to gs://$GCS_BUCKET_NAME..."
gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET_NAME" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.objectAdmin" >/dev/null

echo "Granting $PERSONAL_EMAIL permission to impersonate $SA_EMAIL..."
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --member="user:$PERSONAL_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null

if grep -q "^GCS_SIGNING_SERVICE_ACCOUNT=" "$ENV_FILE"; then
  # macOS/BSD sed needs an explicit (empty) backup extension for -i.
  sed -i.bak "s|^GCS_SIGNING_SERVICE_ACCOUNT=.*|GCS_SIGNING_SERVICE_ACCOUNT=$SA_EMAIL|" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
  echo "Updated GCS_SIGNING_SERVICE_ACCOUNT in $ENV_FILE."
else
  echo "Add this line to $ENV_FILE:"
  echo "  GCS_SIGNING_SERVICE_ACCOUNT=$SA_EMAIL"
fi
echo

# === Step 4: permission to call a deployed agent ============================
echo "=== Step 4: aiplatform.user for calling deployed agents ==="
gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
  --member="user:$PERSONAL_EMAIL" \
  --role="roles/aiplatform.user" \
  --condition=None >/dev/null
echo "Granted roles/aiplatform.user to $PERSONAL_EMAIL."
echo

# === Step 5: service account for the deployed frontend ======================
echo "=== Step 5: Cloud Run frontend service account ==="
FE_SA_NAME="social-spark-frontend"
FE_SA_EMAIL="$FE_SA_NAME@$GOOGLE_CLOUD_PROJECT.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$FE_SA_EMAIL" --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
  echo "Service account $FE_SA_EMAIL already exists, skipping create."
else
  gcloud iam service-accounts create "$FE_SA_NAME" \
    --project="$GOOGLE_CLOUD_PROJECT" \
    --display-name="Social Spark frontend (Cloud Run)" >/dev/null
  echo "Created $FE_SA_EMAIL."
fi
gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
  --member="serviceAccount:$FE_SA_EMAIL" \
  --role="roles/aiplatform.user" \
  --condition=None >/dev/null
echo "Granted roles/aiplatform.user to $FE_SA_EMAIL."
echo "Deploy the frontend with:  --service-account=$FE_SA_EMAIL"
echo

# === Step 6: Buffer API key in Secret Manager ==============================
echo "=== Step 6: Buffer API key in Secret Manager ==="
BUFFER_SECRET_ID="buffer-api-key"
buffer_key="$(grep -m1 "^BUFFER_API_KEY=" "$ENV_FILE" | cut -d= -f2- | tr -d "\r\n\"'" || true)"
if [[ -z "$buffer_key" ]]; then
  echo "BUFFER_API_KEY not set in $ENV_FILE — skipping."
elif gcloud secrets describe "$BUFFER_SECRET_ID" --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
  echo "Secret $BUFFER_SECRET_ID already exists, skipping create."
else
  printf %s "$buffer_key" | gcloud secrets create "$BUFFER_SECRET_ID" \
    --project="$GOOGLE_CLOUD_PROJECT" --data-file=- >/dev/null
  echo "Created secret $BUFFER_SECRET_ID."
fi
unset buffer_key
echo

# === Step 7: Secret Manager access for deployed Agent Identity =============
echo "=== Step 7: Deployed agent access to Secret Manager ==="
if gcloud secrets describe "$BUFFER_SECRET_ID" --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
  echo "Granting secretAccessor on $BUFFER_SECRET_ID to every Agent Identity..."
  gcloud secrets add-iam-policy-binding "$BUFFER_SECRET_ID" \
    --project="$GOOGLE_CLOUD_PROJECT" \
    --member="$AGENT_IDENTITY_MEMBER" \
    --role="roles/secretmanager.secretAccessor" >/dev/null

  # Also grant to Agent Engine service agent
  gcloud secrets add-iam-policy-binding "$BUFFER_SECRET_ID" \
    --project="$GOOGLE_CLOUD_PROJECT" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" >/dev/null

  echo "Granted secretAccessor to Agent Identity principalSet and Agent Engine service agent."
  echo "Deploy the agent with env var:  BUFFER_API_KEY_SECRET=$BUFFER_SECRET_ID"
fi
echo

# === Step 8: Bucket write access for deployed Agent Identity ===============
echo "=== Step 8: Deployed agent write access to bucket ==="
if [[ -n "${GCS_BUCKET_NAME:-}" ]]; then
  echo "Granting roles/storage.objectAdmin on gs://$GCS_BUCKET_NAME to every Agent Identity..."
  gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET_NAME" \
    --member="$AGENT_IDENTITY_MEMBER" \
    --role="roles/storage.objectAdmin" >/dev/null
  echo "Granted roles/storage.objectAdmin on gs://$GCS_BUCKET_NAME to every Agent Identity."
fi
echo

echo "Done."
