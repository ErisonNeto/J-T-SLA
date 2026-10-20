# J&T SLA Dashboard

Dashboard FastAPI + HTML/CSS/JS com banco PostgreSQL no Supabase.

## Variável de ambiente obrigatória

No Render, configure:

DATABASE_URL=<URI do Session Pooler do Supabase>

Nunca envie o arquivo `.env` para o GitHub.

## Render

Build Command:

pip install -r requirements.txt

Start Command:

uvicorn main:app --host 0.0.0.0 --port $PORT
