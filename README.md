# AI-Sentinal (Stage 02)

AI-Sentinal is a Flask-based deepfake detection prototype with:
- video upload + model inference
- server-side authentication (manual + optional Google OAuth)
- SQLite persistence for users and scan history
- dashboard/detected pages driven by real data

## Tech Stack
- Python 3.10+
- Flask
- PyTorch + torchvision
- OpenCV + face-recognition (dlib)
- SQLite
- Waitress (production WSGI)

## Project Structure
- `app.py` - main Flask app
- `wsgi.py` - WSGI entrypoint for production servers
- `templates/` - HTML templates
- `static/` - CSS/JS assets
- `model/df_model.pt` - deepfake model file
- `Uploaded_Files/` - temporary upload directory
- `aisentinal.db` - SQLite DB (auto-created)

## 1) Prerequisites

### Recommended (Windows reliable path)
Use **Conda** because `dlib` can fail with plain pip.

Install Miniconda (once):
```powershell
winget install -e --id Anaconda.Miniconda3
```

Then open **Anaconda PowerShell Prompt**.

### Alternative
You can use `venv + pip`, but if `dlib` build fails, switch to Conda.

## 2) Local Setup (Conda)

```powershell
cd E:\AI-Sentinal

conda create -n aisentinal python=3.10 -y
conda activate aisentinal

# Install dlib/face-recognition from conda-forge for reliability
conda install -c conda-forge dlib face_recognition cmake -y

# Install app dependencies
pip install -r requirements.txt
```

## 3) Environment Configuration

Copy env template:
```powershell
Copy-Item .env.example .env
```

Update `.env` as needed:
- `SECRET_KEY` (set a strong random value)
- `PORT` (default `5000`)
- `FLASK_DEBUG` (`1` for dev, `0` for prod-like)
- `MAX_UPLOAD_MB`
- `SESSION_COOKIE_SECURE` (`1` when running HTTPS)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (optional)

## 4) Run (Development)

```powershell
python app.py
```

Open:
- `http://127.0.0.1:5000`

Health check:
- `http://127.0.0.1:5000/health`

## 5) Default Admin User

Auto-seeded on first run:
- Username: `admin`
- Password: `admin`

Change this immediately in shared environments.

## 6) Authentication

### Manual Auth
- Signup: `/signup`
- Login: `/login`

### Google OAuth (optional)
Set in `.env`:
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Then restart app. "Continue with Google" appears on login page.

## 7) Production-style Run (Waitress)

```powershell
waitress-serve --listen=0.0.0.0:5000 wsgi:application
```

## 8) Docker Run

```powershell
docker compose up --build
```

App:
- `http://127.0.0.1:5000`

## 9) Tests and CI

Install CI/test deps:
```powershell
pip install -r requirements-ci.txt
```

Run tests:
```powershell
pytest -q
```

Lint:
```powershell
ruff check app.py tests
```

GitHub Actions workflow file:
- `.github/workflows/ci.yml`

## 10) Core Route Flow
- `/Detect` (POST): upload video and run inference
- detection result + confidence stored in DB
- dashboard (`/dashboard.html`) and detected queue (`/detected.html`) read DB records

## 11) Common Troubleshooting

### `Failed building wheel for dlib`
Use Conda path above:
```powershell
conda install -c conda-forge dlib face_recognition cmake -y
```

### PowerShell activation blocked
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Port already in use
Set another port in `.env` (`PORT=5001`) and restart.

### Model missing
Ensure file exists:
- `model/df_model.pt`

## 12) Security Notes
- Keep `SECRET_KEY` private.
- Enable `SESSION_COOKIE_SECURE=1` on HTTPS deployments.
- Do not keep default `admin/admin` in real environments.
- Rate limiting is in-memory (sufficient for local/small usage).

## 13) Stage 02 Scope Implemented
- Config via env vars
- Structured logs + startup checks + health endpoint
- Server-side auth + manual signup/login + optional Google OAuth
- CSRF protection
- Upload size/type checks + rate limiting
- SQLite persistence for users/scans/errors
- Real dashboard/detected data
- WSGI, Docker, env template, tests, CI workflow