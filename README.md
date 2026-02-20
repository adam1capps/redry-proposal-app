# ReDry Proposal Builder

Web app for generating branded ReDry proposals as PDFs and shareable client links with digital acceptance.

## Deploy to Render (recommended, free tier)

1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Render will auto-detect the `render.yaml` and Dockerfile
5. Click Deploy

Your app will be live at `https://your-app-name.onrender.com`

## Deploy to Railway

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. Railway will auto-detect the Dockerfile
5. It will assign a public URL automatically

## Deploy to Fly.io

```bash
fly launch --name redry-proposals
fly deploy
```

## Local Development

```bash
pip install -r requirements.txt
python server.py
# Open http://localhost:5000
```

## How It Works

**Team side** (`/`): Fill in client info, project details, pricing, upload a vent map. Two buttons:
- **Download PDF** generates the exact branded reportlab PDF (matching the L.D. Tebben proposal)
- **Create Client Link** generates a shareable URL

**Client side** (`/proposal/<id>`): Client sees the full proposal online, can download the PDF, and accept/sign digitally. Acceptance is recorded server-side.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/generate-pdf` | POST | Generate and download PDF |
| `/api/generate-proposal-link` | POST | Create shareable client link + PDF |
| `/api/proposal/<id>` | GET | Get proposal config JSON |
| `/api/proposal/<id>/pdf` | GET | Download saved PDF |
| `/api/proposal/<id>/ventmap` | GET | Get vent map image |
| `/api/proposal/<id>/accept` | POST | Record client acceptance |
| `/api/proposals` | GET | List all proposals |

## File Structure

```
├── server.py               # Flask API
├── proposal_generator.py   # ReportLab PDF engine
├── static/index.html       # React frontend (CDN-loaded, no build step)
├── redry_logo.jpg          # Brand logo
├── Dockerfile              # Container config
├── render.yaml             # Render.com config
├── railway.json            # Railway config
├── Procfile                # Heroku-style config
├── requirements.txt        # Python deps
├── uploads/                # Temp vent map uploads
└── proposals/              # Saved proposals + acceptance records
```

## Note on Storage

The free tier on Render/Railway uses ephemeral storage, meaning saved proposals will be lost on redeploy. For production use, you would want to add a database (Postgres) or cloud storage (S3) for persistence. The current file-based storage works well for initial use and testing.
