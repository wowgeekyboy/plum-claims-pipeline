# Streamlit Community Cloud deployment guide

This guide walks through deploying the Plum Claims Pipeline to Streamlit Community Cloud (free).

## Prerequisites
- GitHub account (you have one: `wowgeekyboy`)
- Streamlit Cloud account (sign up at https://share.streamlit.io with your GitHub)

## Steps

### 1. Push to GitHub (already done)
The repo is at: https://github.com/wowgeekyboy/plum-claims-pipeline

### 2. Sign in to Streamlit Cloud
- Go to https://share.streamlit.io
- Click "Sign in with GitHub"
- Authorize Streamlit to access your repos

### 3. Create a new app
- Click "New app" (top right)
- Fill in the form:
  - **Repository**: `wowgeekyboy/plum-claims-pipeline`
  - **Branch**: `main`
  - **Main file path**: `frontend/streamlit_app.py`
  - **App URL**: pick a custom subdomain (e.g. `plum-claims`)
- Click "Deploy"

### 4. Wait for build (1-3 minutes)
Streamlit will:
- Install Python 3.11
- Install requirements from `requirements.txt`
- Start the app

### 5. Add secrets (optional)
The current app runs in **local mode** (no API key needed for tests). If you want to use Gemini for production document extraction, add a secret:
- Go to the app settings (gear icon) → "Secrets"
- Add:
  ```toml
  GOOGLE_API_KEY = "your-gemini-api-key"
  ```

### 6. Share the URL
Your app is live at: `https://plum-claims.streamlit.app` (or whatever subdomain you picked)

## What the deployed app does

- **Submit Claim**: form for submitting a claim, shows the decision + full trace
- **Test Cases**: runs all 12 test cases from the assignment with one click
- **About**: system info

## What works in the deployed app
- All 6 agents execute
- LangGraph orchestrator runs end-to-end
- All 12 test cases pass
- Color-coded decision display
- Full agent trace timeline
- Test case runner with progress bar

## What doesn't work (yet)
- Production mode Gemini extraction (the test mode works)
- Persistent claim storage (Streamlit Cloud has ephemeral storage)
- Real document uploads with OCR

## Updating the app
Just push to GitHub:
```bash
git add .
git commit -m "your changes"
git push
```
Streamlit Cloud will auto-redeploy in ~30 seconds.

## Cost
- Streamlit Community Cloud: FREE
- 1 GB RAM, 1 CPU per app
- App sleeps after 7 days of inactivity (wakes on next visit)
- Sufficient for demo / assignment review

## Limitations to be aware of
- The app sleeps when not in use (cold start ~10s)
- Resource limits (1 GB RAM) may be tight for the LangGraph import
- If you hit memory issues, consider deploying to Render or Hugging Face Spaces
