from fastapi import FastAPI

app = FastAPI(title="Sample API")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "FastAPI is running"}

@app.get("/health")
def health():
    return {"healthy": True}
