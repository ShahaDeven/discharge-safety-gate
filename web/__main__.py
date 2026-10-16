"""Run the console: python -m web   (then open http://localhost:8001)

Serves the built frontend from web/frontend/dist; build it once with
    cd web/frontend && npm install && npm run build
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.api:app", host="127.0.0.1", port=8001)
