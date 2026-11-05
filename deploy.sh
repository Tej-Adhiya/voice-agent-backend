#!/bin/bash
# ============================================================
# Voice Agent — Cloud Run Deployment Script
# Project: YOUR_GCP_PROJECT | Region: europe-west1
# Run this AFTER Firebase is upgraded to Blaze plan
# ============================================================

set -e  # Exit on any error

PROJECT_ID="YOUR_GCP_PROJECT"
REGION="europe-west1"
REPO="voice-agent-repo"
SERVICE="voice-agent"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:latest"

echo "🚀 Starting Voice Agent deployment to Cloud Run..."

# Step 1: Set project
gcloud config set project $PROJECT_ID

# Step 2: Enable required APIs
echo "📡 Enabling Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project=$PROJECT_ID

# Step 3: Create Artifact Registry repository (safe to re-run)
echo "📦 Creating Artifact Registry repository..."
gcloud artifacts repositories create $REPO \
  --repository-format=docker \
  --location=$REGION \
  --description="Voice Agent Docker images" \
  --project=$PROJECT_ID 2>/dev/null || echo "Repository already exists, skipping."

# Step 4: Authenticate Docker with Artifact Registry
echo "🔐 Authenticating Docker..."
gcloud auth configure-docker $REGION-docker.pkg.dev --quiet

# Step 5: Build and push Docker image (linux/amd64 for Cloud Run compatibility)
echo "🐳 Building Docker image..."
docker build --platform linux/amd64 -t $IMAGE .

echo "⬆️  Pushing image to Artifact Registry..."
docker push $IMAGE

# Step 6: Load env vars from .env file
echo "🔑 Loading environment variables from .env..."
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
  echo "   Loaded: SUPABASE_URL, SUPABASE_SERVICE_KEY, CLIENT_ID, CLIENT_SECRET, VAPI_PRIVATE_API_KEY"
else
  echo "⚠️  No .env file found — set env vars manually in Cloud Run console."
fi

# Step 7: Deploy to Cloud Run
echo "🌍 Deploying to Cloud Run..."
gcloud run deploy $SERVICE \
  --image=$IMAGE \
  --platform=managed \
  --region=$REGION \
  --allow-unauthenticated \
  --port=8000 \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=5 \
  --set-env-vars="SUPABASE_URL=$SUPABASE_URL,SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY,CLIENT_ID=$CLIENT_ID,CLIENT_SECRET=$CLIENT_SECRET,VAPI_PRIVATE_API_KEY=$VAPI_PRIVATE_API_KEY" \
  --project=$PROJECT_ID

# Step 8: Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE \
  --platform=managed \
  --region=$REGION \
  --project=$PROJECT_ID \
  --format='value(status.url)')

echo ""
echo "✅ Deployment complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 Live URL: $SERVICE_URL"
echo "🏥 Health:   $SERVICE_URL/health"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 Next steps:"
echo "   1. Update vapi_tools.json URLs from Ngrok → $SERVICE_URL"
echo "   2. Update Vapi dashboard webhook URL → $SERVICE_URL/webhooks/vapi"
echo "   3. Test with: curl $SERVICE_URL/health"
