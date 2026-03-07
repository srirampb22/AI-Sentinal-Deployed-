from app import app, init_db, run_startup_checks

# Initialize database and run startup checks when served by WSGI server.
init_db()
run_startup_checks()

application = app