# Google Calendar Setup

This guide walks you through connecting schedulebot to your Google Calendar.

## 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a project** (top bar) -> **New Project**
3. Name it (e.g. `schedulebot`) and click **Create**
4. Make sure the new project is selected in the top bar

## 2. Enable Google Calendar API

1. Go to **APIs & Services** -> **Library**
2. Search for **Google Calendar API**
3. Click on it and press **Enable**

## 3. Configure OAuth Consent Screen

1. Go to **APIs & Services** -> **OAuth consent screen**
2. Select **External** user type -> **Create**
3. Fill in:
   - App name: `schedulebot`
   - User support email: your email
   - Developer contact email: your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes**
   - Find `Google Calendar API` -> `.../auth/calendar`
   - Check it and click **Update**
6. Click **Save and Continue**
7. On the **Test users** page, click **Add Users**
   - Add your Gmail address (the one with the calendar you want to use)
8. Click **Save and Continue**

> **Note:** While in "Testing" mode, only the test users you added can authorize. This is fine for personal use. To let others authorize their calendars, you would need to publish the app.

## 4. Create OAuth Credentials

1. Go to **APIs & Services** -> **Credentials**
2. Click **Create Credentials** -> **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `schedulebot` (or anything)
5. Click **Create**
6. Click **Download JSON** (the download button next to the client ID)
7. Save the file as `credentials.json` in your schedulebot project directory

## 5. Authorize schedulebot

Run the check command:

```bash
schedulebot check
```

This will:
1. Detect `credentials.json` in your project directory
2. Open your browser to Google's authorization page
3. Sign in with the Google account you added as a test user
4. Grant calendar access to schedulebot
5. Save the authorization token as `token.json`

After authorization, you should see:

```
[OK] Google Calendar authenticated
```

## 6. Verify It Works

```bash
schedulebot slots
```

This shows your available slots based on your calendar's free/busy data. If you see slots listed, everything is connected.

## Troubleshooting

### "Access blocked: This app's request is invalid"
- Make sure you downloaded the **Desktop app** credentials (not Web application)
- Re-download `credentials.json` and try again

### "The caller does not have permission"
- Make sure your Google account is added as a **test user** in the OAuth consent screen

### "credentials.json not found"
- The file must be in your current working directory (where you run `schedulebot`)
- Or set the path in `config.yaml` under `calendar.credentials_path`

### Token expired
- schedulebot automatically refreshes expired tokens
- If refresh fails, delete `token.json` and run `schedulebot check` again

## Docker / Railway Deployment

For containerized deployments where you can't open a browser:

1. First authorize locally (steps above) to get `token.json`
2. Base64-encode both files:
   ```bash
   export GOOGLE_CREDENTIALS_JSON=$(base64 < credentials.json)
   export GOOGLE_TOKEN_JSON=$(base64 < token.json)
   ```
3. Set these as environment variables in your deployment platform
4. schedulebot will read credentials from env vars when files are not present

## Security Notes

- `credentials.json` and `token.json` are in `.gitignore` by default
- Never commit these files to git
- The token grants full calendar access to your account -- keep it secure
- For production, consider using a service account instead of OAuth (not yet supported)
