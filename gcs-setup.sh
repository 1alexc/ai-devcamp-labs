#!/usr/bin/env bash
# One-time manual GCP setup for this project — a growing checklist, not a
# single-purpose script. Every step below is written to be idempotent, so
# it's safe to re-run the whole file each time a new step is added rather
# than tracking which ones you've already done.
#
# Edit PERSONAL_EMAIL to your own Google account once, then run:
#   ./gcs-setup.sh
set -euo pipefail
cd "$(dirname "$0")"

PERSONAL_EMAIL="sonika.janagill@gmail.com"  # <-- change to your own Google account

# === Step 0: gcloud auth ====================================================
echo "=== Step 0: gcloud auth ==="
current_account="$(gcloud config get-value account 2>/dev/null || true)"
if [[ "$current_account" == "$PERSONAL_EMAIL" ]]; then
  echo "Already logged in as $PERSONAL_EMAIL, skipping."
else
  gcloud auth login "$PERSONAL_EMAIL"
fi

ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
if [[ -f "$ADC_FILE" ]]; then
  echo "Application Default Credentials already set up, skipping."
else
  gcloud auth application-default login
fi
echo

# === Step 1: GCS image hosting for Buffer (signed URLs, no key file) =======
# Lets Buffer posts carry an image, via a signed URL rather than a public
# bucket grant (Public Access Prevention blocks that outright on managed
# projects — see docs/LEARNINGS.md). Optional: not part of Lab 1's taught
# steps, which keep Buffer text-only by design. Skipped entirely if
# GCS_BUCKET_NAME isn't set in backend/social_poster/.env yet.
echo "=== Step 1: GCS image hosting for Buffer ==="
ENV_FILE="backend/social_poster/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "skipping — $ENV_FILE not found (copy .env.example there first)."
else
  set -a
  source "$ENV_FILE"
  set +a
  if [[ -z "${GCS_BUCKET_NAME:-}" ]]; then
    echo "skipping — GCS_BUCKET_NAME not set in $ENV_FILE."
  elif [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]]; then
    echo "skipping — GOOGLE_CLOUD_PROJECT not set in $ENV_FILE."
  else
    SA_NAME="social-spark-uploader"
    SA_EMAIL="${SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

    echo "Project:     $GOOGLE_CLOUD_PROJECT"
    echo "Bucket:      $GCS_BUCKET_NAME"
    echo "Uploader SA: $SA_EMAIL"

    if gcloud iam service-accounts describe "$SA_EMAIL" --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
      echo "1/3  Service account already exists, skipping create."
    else
      echo "1/3  Creating service account..."
      gcloud iam service-accounts create "$SA_NAME" \
        --project="$GOOGLE_CLOUD_PROJECT" \
        --display-name="Social Spark image uploader (signs GCS URLs, never downloads a key)"
    fi

    echo "2/3  Granting $SA_EMAIL write access to gs://$GCS_BUCKET_NAME..."
    gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET_NAME" \
      --member="serviceAccount:$SA_EMAIL" \
      --role="roles/storage.objectAdmin" >/dev/null

    echo "3/3  Granting $PERSONAL_EMAIL permission to impersonate $SA_EMAIL..."
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
  fi
fi
echo

# === Step 2: (add the next one-time manual GCP step here) ==================

echo "Done."
