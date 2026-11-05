#!/bin/bash
# ============================================================
# Voice Agent — Cloud Build Redeploy Script
# Builds the container in Google Cloud Build (no local Docker needed).
# Usage: ./redeploy.sh
# ============================================================

set -e

PROJECT_ID="YOUR_GCP_PROJECT"
REGION="europe-west1"
SERVICE="voice-agent"

echo "🚀 Deploying $SERVICE to Cloud Run via Cloud Build..."

gcloud run deploy $SERVICE \
  --source . \
  --platform=managed \
  --region=$REGION \
  --project=$PROJECT_ID \
  --quiet

SERVICE_URL=$(gcloud run services describe $SERVICE \
  --platform=managed \
  --region=$REGION \
  --project=$PROJECT_ID \
  --format='value(status.url)')

echo ""
echo "✅ Redeployed! Live at: $SERVICE_URL"
